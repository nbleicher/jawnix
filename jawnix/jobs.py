from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from .models import Job, JobStatus

# Metrics emit can fan out to tens of thousands of HTTP calls. Keep it behind
# customer-facing work (Telegram, email, allocate, deliver) so a large batch
# never starves approval/delivery notifications again.
_LOW_PRIORITY_JOB_KINDS = frozenset({"emit_lead_assigned"})


def enqueue_job(session: Session, kind: str, request_id: uuid.UUID | None = None, payload: dict | None = None) -> Job:
    job = Job(kind=kind, request_id=request_id, payload=payload or {}, status=JobStatus.queued.value)
    session.add(job)
    session.flush()
    return job


def higher_priority_job_waiting(session: Session, *, now: datetime | None = None) -> bool:
    """True when a non-metrics job is ready for the worker right now."""
    now = now or datetime.now(timezone.utc)
    return (
        session.scalar(
            select(Job.id)
            .where(
                Job.status == JobStatus.queued.value,
                Job.run_after <= now,
                Job.kind.notin_(_LOW_PRIORITY_JOB_KINDS),
            )
            .limit(1)
        )
        is not None
    )


def claim_next_job(session: Session, worker_id: str, lock_timeout_seconds: int = 900) -> Job | None:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=lock_timeout_seconds)
    priority = case(
        (Job.kind.in_(_LOW_PRIORITY_JOB_KINDS), 1),
        else_=0,
    )
    job = session.scalar(
        select(Job)
        .where(
            or_(
                and_(Job.status == JobStatus.queued.value, Job.run_after <= now),
                and_(Job.status == JobStatus.running.value, Job.locked_at <= stale_before),
            )
        )
        .order_by(priority, Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not job:
        return None
    job.status = JobStatus.running.value
    job.locked_at = now
    job.locked_by = worker_id
    job.attempts += 1
    session.flush()
    return job
