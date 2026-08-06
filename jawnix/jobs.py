from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .models import Job, JobStatus

# lead.assigned fan-out runs on its own worker lane so Telegram, email,
# allocate, and deliver never share a claim loop with metrics ingest.
METRICS_JOB_KINDS = frozenset({"emit_lead_assigned"})
WORKER_LANE_DEFAULT = "default"
WORKER_LANE_METRICS = "metrics"
_VALID_WORKER_LANES = frozenset({WORKER_LANE_DEFAULT, WORKER_LANE_METRICS})


def normalize_worker_lane(lane: str | None) -> str:
    value = (lane or WORKER_LANE_DEFAULT).strip().lower()
    if value not in _VALID_WORKER_LANES:
        raise ValueError(
            f"Unknown worker lane {lane!r}; expected "
            f"{WORKER_LANE_DEFAULT!r} or {WORKER_LANE_METRICS!r}"
        )
    return value


def job_lane(kind: str) -> str:
    """Which worker lane owns this job kind."""
    if kind in METRICS_JOB_KINDS:
        return WORKER_LANE_METRICS
    return WORKER_LANE_DEFAULT


def enqueue_job(session: Session, kind: str, request_id: uuid.UUID | None = None, payload: dict | None = None) -> Job:
    job = Job(kind=kind, request_id=request_id, payload=payload or {}, status=JobStatus.queued.value)
    session.add(job)
    session.flush()
    return job


def higher_priority_job_waiting(
    session: Session,
    *,
    lane: str = WORKER_LANE_DEFAULT,
    now: datetime | None = None,
) -> bool:
    """True when a higher-priority job is ready on the same worker lane.

    Only meaningful on the default lane (metrics is a single-kind lane).
    """
    lane = normalize_worker_lane(lane)
    if lane == WORKER_LANE_METRICS:
        return False
    now = now or datetime.now(timezone.utc)
    return (
        session.scalar(
            select(Job.id)
            .where(
                Job.status == JobStatus.queued.value,
                Job.run_after <= now,
                Job.kind.notin_(METRICS_JOB_KINDS),
            )
            .limit(1)
        )
        is not None
    )


def claim_next_job(
    session: Session,
    worker_id: str,
    lock_timeout_seconds: int = 900,
    *,
    lane: str = WORKER_LANE_DEFAULT,
) -> Job | None:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=lock_timeout_seconds)
    lane = normalize_worker_lane(lane)
    if lane == WORKER_LANE_METRICS:
        kind_filter = Job.kind.in_(METRICS_JOB_KINDS)
    else:
        kind_filter = Job.kind.notin_(METRICS_JOB_KINDS)
    job = session.scalar(
        select(Job)
        .where(
            kind_filter,
            or_(
                and_(Job.status == JobStatus.queued.value, Job.run_after <= now),
                and_(Job.status == JobStatus.running.value, Job.locked_at <= stale_before),
            ),
        )
        .order_by(Job.run_after, Job.id)
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
