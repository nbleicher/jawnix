"""A new copy of an expired batch file, asked for by the Customer (Q14).

Delivered files are kept for 30 days. After that a Customer can ask for a new
copy from the batch page instead of emailing Noah: the same leads, rebuilt byte
for byte from the batch's committed Distribution Events through the rebuild
path the operator's Regenerate uses, and emailed when it is ready.

Asking is cheap and the rebuild is not, so the ask only queues a job. One copy
per batch is in flight at a time, each Customer may ask a few times a day, and
every ask and every rebuild leaves an Audit Entry.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .activity import record_activity
from .config import Settings, get_settings
from .fulfillment import artifact_available
from .jobs import enqueue_job
from .milestone_emails import MILESTONE_EMAIL_JOB
from .models import (
    BatchArtifact,
    DistributionEvent,
    Job,
    JobStatus,
    LeadRequest,
    RequestStatus,
)

#: The worker job that rebuilds one batch's file for its Customer.
NEW_COPY_JOB = "make_new_copy"

#: How many new copies one Customer may ask for in a rolling window.
NEW_COPY_LIMIT = 5
NEW_COPY_WINDOW = timedelta(hours=24)

#: The Audit Entry actor for rebuilds the worker makes on a Customer's behalf.
NEW_COPY_ACTOR = "system:new-copy"

_IN_FLIGHT = (JobStatus.queued.value, JobStatus.running.value)


class ArtifactRebuildError(Exception):
    """A rebuild the batch cannot support as it stands."""

    def __init__(self, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class NewCopyRefused(Exception):
    """A new-copy ask the Customer cannot make right now, in their words."""

    def __init__(self, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _artifact_summary(artifact: BatchArtifact | None) -> dict | None:
    if artifact is None:
        return None
    return {
        "artifactId": artifact.id,
        "available": False,
        "expiresAt": (
            artifact.expires_at.isoformat()
            if artifact.expires_at is not None
            else None
        ),
    }


def rebuild_expired_artifact(
    db: Session,
    item: LeadRequest,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[BatchArtifact, dict | None]:
    """Rebuild an expired or missing batch file from its committed events.

    The one rebuild path, shared by the operator's Regenerate and the
    Customer's new copy. ``generate_artifact`` keeps the file's name, parts,
    and CSV layout, so the rebuilt file is byte-identical to the one first
    delivered. Returns the artifact and a summary of the one it replaced.
    The caller holds the batch's row lock and records the Audit Entry.
    """

    from .allocation import generate_artifact

    events = list(
        db.scalars(
            select(DistributionEvent)
            .where(DistributionEvent.request_id == item.id)
            .order_by(DistributionEvent.id)
        )
    )
    if len(events) != item.lead_count:
        raise ArtifactRebuildError("Committed Distribution Events are incomplete.")
    artifact = item.artifact
    # Shares the offer surfaces' predicate: if these ever disagreed, a page
    # would offer a rebuild this refuses.
    if artifact_available(artifact, now=now):
        raise ArtifactRebuildError("Batch Artifact has not expired.")
    previous = _artifact_summary(artifact)
    return generate_artifact(db, item, events, settings), previous


def pending_copy_ids(db: Session, request_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """The batches among ``request_ids`` with a new copy on the way."""

    if not request_ids:
        return set()
    return set(
        db.scalars(
            select(Job.request_id).where(
                Job.kind == NEW_COPY_JOB,
                Job.request_id.in_(request_ids),
                Job.status.in_(_IN_FLIGHT),
            )
        )
    )


def can_ask_for_copy(item: LeadRequest, *, now: datetime | None = None) -> bool:
    """A delivered batch whose file has expired or gone missing."""

    return (
        item.status == RequestStatus.delivered.value
        and item.artifact is not None
        and not artifact_available(item.artifact, now=now)
    )


def _copies_asked_since(db: Session, customer_id: int, since: datetime) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Job)
            .join(LeadRequest, LeadRequest.id == Job.request_id)
            .where(
                Job.kind == NEW_COPY_JOB,
                LeadRequest.agent_id == customer_id,
                Job.created_at >= since,
            )
        )
        or 0
    )


def request_new_copy(
    db: Session,
    *,
    item: LeadRequest,
    actor_id: object,
    now: datetime | None = None,
) -> tuple[Job, bool]:
    """Queue a new copy of this batch's expired file.

    ``item`` must already be read with its row lock, so two asks for the same
    batch cannot both queue a rebuild. Returns the job and whether this call
    queued it; asking again while a copy is on the way returns that copy.
    """

    moment = now or datetime.now(timezone.utc)
    if item.status != RequestStatus.delivered.value or item.artifact is None:
        raise NewCopyRefused(
            "This batch has no file to copy yet.",
            status_code=404,
        )
    if artifact_available(item.artifact, now=moment):
        raise NewCopyRefused(
            "This file has not expired. Download it from the batch page."
        )
    existing = db.scalar(
        select(Job)
        .where(
            Job.kind == NEW_COPY_JOB,
            Job.request_id == item.id,
            Job.status.in_(_IN_FLIGHT),
        )
        .order_by(Job.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing, False
    committed = int(
        db.scalar(
            select(func.count())
            .select_from(DistributionEvent)
            .where(DistributionEvent.request_id == item.id)
        )
        or 0
    )
    support = get_settings().support_email
    if committed != item.lead_count:
        raise NewCopyRefused(
            "We cannot rebuild this file automatically. "
            f"Email {support} and we will send you a copy."
        )
    asked = _copies_asked_since(db, item.agent_id, moment - NEW_COPY_WINDOW)
    if asked >= NEW_COPY_LIMIT:
        raise NewCopyRefused(
            f"You have asked for {NEW_COPY_LIMIT} new copies in the last 24 "
            f"hours. Try again tomorrow, or email {support}.",
            status_code=429,
        )
    job = enqueue_job(db, NEW_COPY_JOB, item.id)
    record_activity(
        db,
        action="batch_artifact_copy_requested",
        target_type="batch_request",
        target_id=item.id,
        actor_id=actor_id,
        reason="Customer asked for a new copy of the expired file.",
        details={
            "requestId": str(item.id),
            "jobId": job.id,
            "artifact": _artifact_summary(item.artifact),
        },
    )
    return job, True


def make_new_copy(
    db: Session,
    request_id: uuid.UUID,
    settings: Settings,
    *,
    job_id: int,
) -> BatchArtifact | None:
    """The worker side: rebuild the file, audit it, and email the Customer.

    Returns None without rebuilding when the file is already live again, for
    instance because the operator regenerated it first.
    """

    item = db.scalar(
        select(LeadRequest).where(LeadRequest.id == request_id).with_for_update()
    )
    if item is None:
        raise LookupError("Request was not found.")
    if artifact_available(item.artifact):
        return None
    artifact, previous = rebuild_expired_artifact(db, item, settings)
    record_activity(
        db,
        action="batch_artifact_regenerated",
        target_type="batch_request",
        target_id=item.id,
        actor_id=NEW_COPY_ACTOR,
        reason="Customer asked for a new copy of the expired file.",
        details={
            "before": previous,
            "after": {"artifactId": artifact.id, "available": True},
            "requestId": str(item.id),
            "sha256": artifact.sha256,
            "expiresAt": (
                artifact.expires_at.isoformat()
                if artifact.expires_at is not None
                else None
            ),
        },
    )
    enqueue_job(
        db,
        MILESTONE_EMAIL_JOB,
        item.id,
        payload={
            "milestone": "new_copy",
            # One email per copy: a later copy of the same batch is a new message.
            "idempotency_key": f"jawnix-request/{item.id}/new_copy/{job_id}",
        },
    )
    return artifact
