from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jawnix.config import Settings
from jawnix.jobs import enqueue_job
from jawnix.models import (
    Lead,
    LeadRequest,
    NightlyReview,
    RequestStatus,
    ScraperRun,
)

from .migration import import_scraper_sqlite


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sync_scraper(session: Session, settings: Settings, source: str | None = None, force: bool = False) -> dict:
    if source and source.lower() == "nppes":
        raise ValueError("All future acquisition must use the Google Maps Scraper.")
    if settings.scraper_command:
        command = shlex.split(settings.scraper_command)
        if source:
            command.extend(["--sources", source])
        environment = os.environ.copy()
        subprocess.run(command, check=True, env=environment)
    path = Path(settings.scraper_db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Scraper database was not found: {path}")
    source_name = "google_maps"
    source_version = _file_checksum(path)
    previous = session.scalar(
        select(ScraperRun)
        .where(ScraperRun.source == source_name, ScraperRun.status == "complete")
        .order_by(ScraperRun.finished_at.desc())
        .limit(1)
    )
    if previous and previous.source_version == source_version and not force:
        return {
            "skipped": True,
            "reason": "dataset checksum already synchronized",
            "sourceVersion": source_version,
        }
    run = ScraperRun(source=source_name, source_version=source_version, status="running")
    session.add(run)
    session.flush()
    try:
        result = import_scraper_sqlite(session, path, expected_checksum=source_version)
        run.status = "complete"
        run.checksum = result.get("checksum", "")
        run.rows_seen = result.get("sourceRows", 0)
        run.rows_imported = result.get("imported", 0)
        run.details = result
        run.finished_at = datetime.now(timezone.utc)
        review = NightlyReview(
            scraper_run_id=run.id,
            summary={
                "scraper": {
                    "observed": int(result.get("sourceRows", 0)),
                    "valid": int(result.get("sourceRows", 0))
                    - int(result.get("quarantined", 0)),
                    "new": int(result.get("imported", 0)),
                    "duplicate": max(
                        0,
                        int(result.get("sourceRows", 0))
                        - int(result.get("quarantined", 0))
                        - int(result.get("imported", 0)),
                    ),
                    "quarantined": int(result.get("quarantined", 0)),
                    "anomalous": 0,
                },
                "inventory": {
                    "leads": int(
                        session.scalar(select(func.count(Lead.id))) or 0
                    ),
                    "waitingRequests": int(
                        session.scalar(
                            select(func.count(LeadRequest.id)).where(
                                LeadRequest.status
                                == RequestStatus.waiting_inventory.value
                            )
                        )
                        or 0
                    ),
                },
            },
        )
        session.add(review)
        session.flush()
        enqueue_job(
            session,
            "notify_nightly_review",
            payload={"review_id": str(review.id)},
        )
        enqueue_job(session, "fulfill_round_robin")
        return result
    except Exception as exc:
        run.status = "failed"
        run.details = {"error": str(exc)}
        run.finished_at = datetime.now(timezone.utc)
        raise
