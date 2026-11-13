from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings
from .keyword_generation import purge_generation_drafts
from .milestone_emails import MILESTONE_EMAIL_JOB
from .models import BatchArtifact, Job, JobStatus
from .registration_emails import REGISTRATION_EMAIL_JOB


def expire_batch_files(session: Session, settings: Settings) -> dict[str, int]:
    """Remove only expired CSV files; allocation and artifact metadata are retained."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.batch_retention_days)
    base = Path(settings.batch_dir).resolve()
    removed = missing = skipped = 0
    for artifact in session.scalars(select(BatchArtifact).where(BatchArtifact.created_at < cutoff)):
        path = Path(artifact.path).resolve()
        if not path.is_relative_to(base):
            skipped += 1
            continue
        if path.is_file():
            path.unlink()
            removed += 1
        else:
            missing += 1
    return {"removed": removed, "alreadyMissing": missing, "outsideBatchDir": skipped}


def purge_retained_records(session: Session, settings: Settings) -> dict[str, int]:
    """Purge records whose domain retention window has elapsed."""

    return {
        "keywordGenerationDrafts": purge_generation_drafts(session),
        "completedJobs": purge_completed_jobs(session, settings),
    }


def purge_completed_jobs(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """Delete completed Jobs older than the retention window.

    Failed Jobs stay: the Operations overview is their review queue. Milestone
    and registration email Jobs stay regardless of age because enqueue
    deduplicates against the durable row — deleting one would let a repeated
    status or decision send the customer a second email.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=settings.job_retention_days
    )
    result = session.execute(
        delete(Job).where(
            Job.status == JobStatus.complete.value,
            Job.created_at < cutoff,
            Job.kind.not_in((MILESTONE_EMAIL_JOB, REGISTRATION_EMAIL_JOB)),
        )
    )
    return int(result.rowcount or 0)
