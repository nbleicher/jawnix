from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session

from .models import Job, JobStatus

# lead.assigned fan-out runs on its own worker lane so Telegram, email,
# allocate, and deliver never share a claim loop with metrics ingest.
METRICS_JOB_KINDS = frozenset({"emit_lead_assigned"})
IMPORT_JOB_KINDS = frozenset({"import_lead_upload"})
FULFILLMENT_JOB_KINDS = frozenset({"allocate_request", "fulfill_round_robin", "telegram_action", "telegram_inventory_conflict_action"})
NOTIFICATION_JOB_KINDS = frozenset({"notify_request", "update_notification", "licensed_states_changed", "deliver_request", "send_request_milestone_email", "notify_nightly_review", "update_nightly_review_notification", "notify_telegram_action_failure"})
WORKER_LANE_DEFAULT = "default"
WORKER_LANE_METRICS = "metrics"
WORKER_LANE_FULFILLMENT = "fulfillment"
WORKER_LANE_NOTIFICATIONS = "notifications"
WORKER_LANE_MAINTENANCE = "maintenance"
WORKER_LANE_IMPORTS = "imports"
_VALID_WORKER_LANES = frozenset({WORKER_LANE_DEFAULT, WORKER_LANE_METRICS, WORKER_LANE_FULFILLMENT, WORKER_LANE_NOTIFICATIONS, WORKER_LANE_MAINTENANCE, WORKER_LANE_IMPORTS})


def normalize_worker_lane(lane: str | None) -> str:
    value = (lane or WORKER_LANE_DEFAULT).strip().lower()
    if value not in _VALID_WORKER_LANES:
        raise ValueError(
            f"Unknown worker lane {lane!r}; expected "
            f"one of {', '.join(sorted(_VALID_WORKER_LANES))}"
        )
    return value


def job_lane(kind: str) -> str:
    """Which worker lane owns this job kind."""
    if kind in METRICS_JOB_KINDS:
        return WORKER_LANE_METRICS
    if kind in FULFILLMENT_JOB_KINDS:
        return WORKER_LANE_FULFILLMENT
    if kind in NOTIFICATION_JOB_KINDS:
        return WORKER_LANE_NOTIFICATIONS
    if kind in IMPORT_JOB_KINDS:
        return WORKER_LANE_IMPORTS
    return WORKER_LANE_MAINTENANCE


def _lane_filter(lane: str):
    if lane == WORKER_LANE_METRICS:
        return Job.kind.in_(METRICS_JOB_KINDS)
    if lane == WORKER_LANE_FULFILLMENT:
        return Job.kind.in_(FULFILLMENT_JOB_KINDS)
    if lane == WORKER_LANE_NOTIFICATIONS:
        return Job.kind.in_(NOTIFICATION_JOB_KINDS)
    if lane == WORKER_LANE_IMPORTS:
        return Job.kind.in_(IMPORT_JOB_KINDS)
    if lane == WORKER_LANE_MAINTENANCE:
        return Job.kind.notin_(METRICS_JOB_KINDS | FULFILLMENT_JOB_KINDS | NOTIFICATION_JOB_KINDS | IMPORT_JOB_KINDS)
    # Keep the pre-split deployment's default worker compatible.
    return Job.kind.notin_(METRICS_JOB_KINDS)


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
                _lane_filter(lane),
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
    kind_filter = _lane_filter(lane)
    order = [Job.run_after, Job.id]
    if lane == WORKER_LANE_NOTIFICATIONS:
        # A ready Batch Artifact must not wait behind milestone emails.
        order.insert(0, case((Job.kind == "deliver_request", 0), else_=1))
    job = session.scalar(
        select(Job)
        .where(
            kind_filter,
            or_(
                and_(Job.status == JobStatus.queued.value, Job.run_after <= now),
                and_(Job.status == JobStatus.running.value, Job.locked_at <= stale_before),
            ),
        )
        .order_by(*order)
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
