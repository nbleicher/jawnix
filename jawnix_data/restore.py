from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jawnix.activity import record_activity
from jawnix.config import Settings
from jawnix.models import (
    DatasetPublication,
    ScraperConfiguration,
    ScraperRun,
    SourceSegment,
)

from .migration import file_sha256
from .scraper import lock_scraper_dataset, sync_dataset_version


def validate_or_replay_restored_dataset(
    session: Session,
    settings: Settings,
    dataset_path: Path,
    metadata_path: Path,
    apply: bool = False,
) -> dict:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if int(metadata.get("schemaVersion", 0)) != 1:
        raise ValueError("Unsupported Scraper Dataset metadata schema.")
    restored_version = int(metadata["datasetVersion"])
    restored_checksum = str(metadata["checksum"]).lower()
    actual_checksum = file_sha256(dataset_path)
    if actual_checksum != restored_checksum:
        raise ValueError(
            "Restored Scraper Dataset checksum does not match metadata."
        )
    if apply:
        lock_scraper_dataset(session)
    synchronized_version = int(
        session.scalar(
            select(func.max(DatasetPublication.version)).where(
                DatasetPublication.sync_status == "complete"
            )
        )
        or 0
    )
    if restored_version < synchronized_version:
        raise ValueError(
            "Restored Scraper Dataset is older than PostgreSQL's "
            "synchronized dataset version."
        )
    existing = session.scalar(
        select(DatasetPublication).where(
            DatasetPublication.version == restored_version
        )
    )
    if existing is not None and existing.checksum != restored_checksum:
        raise ValueError(
            "Restored dataset version conflicts with PostgreSQL checksum."
        )
    result = {
        "status": (
            "equal"
            if restored_version == synchronized_version
            else "newer"
        ),
        "datasetVersion": restored_version,
        "checksum": restored_checksum,
        "postgresSynchronizedVersion": synchronized_version,
        "replayRequired": restored_version > synchronized_version,
    }
    if not apply:
        return result
    active_path = Path(settings.scraper_db_path)
    versions_directory = active_path.parent / ".versions"
    archived_path = (
        versions_directory
        / f"{restored_version:020d}-{restored_checksum}.db"
    )
    if existing is None:
        configuration_data = metadata["configuration"]
        configuration_id = uuid.UUID(configuration_data["id"])
        configuration = session.get(
            ScraperConfiguration,
            configuration_id,
        )
        if configuration is None:
            version_collision = session.scalar(
                select(ScraperConfiguration).where(
                    ScraperConfiguration.version
                    == int(configuration_data["version"])
                )
            )
            if version_collision is not None:
                raise ValueError(
                    "Restored configuration version conflicts with "
                    "PostgreSQL."
                )
            configuration = ScraperConfiguration(
                id=configuration_id,
                version=int(configuration_data["version"]),
                checksum=str(configuration_data["checksum"]),
                status="restored",
                anomaly_thresholds=configuration_data.get(
                    "anomalyThresholds"
                )
                or {},
                created_by=uuid.UUID(configuration_data["createdBy"]),
                reason=str(
                    configuration_data.get("reason")
                    or "Restored dataset configuration"
                ),
                segments=[
                    SourceSegment(
                        key=item["key"],
                        niche=item["niche"],
                        query=item["query"],
                        geography=item["geography"],
                        parameters=item.get("parameters") or {},
                    )
                    for item in configuration_data["segments"]
                ],
            )
            session.add(configuration)
            session.flush()
        run = ScraperRun(
            source="google_maps",
            source_version=restored_checksum,
            configuration_id=configuration.id,
            dataset_version=restored_version,
            checksum=restored_checksum,
            status="published",
            details={"restored": True},
            finished_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.flush()
        existing = DatasetPublication(
            version=restored_version,
            checksum=restored_checksum,
            scraper_run_id=run.id,
            configuration_id=configuration.id,
            storage_path=str(archived_path),
            sync_status="pending",
        )
        session.add(existing)
        session.flush()
    versions_directory.mkdir(parents=True, exist_ok=True)
    if not archived_path.is_file():
        shutil.copy2(dataset_path, archived_path)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    staged_active = active_path.parent / f".restore-{uuid.uuid4()}.db"
    shutil.copy2(dataset_path, staged_active)
    os.replace(staged_active, active_path)
    if existing.storage_path != str(archived_path):
        existing.storage_path = str(archived_path)
    sync_result = sync_dataset_version(
        session,
        settings,
        restored_version,
    )
    record_activity(
        session,
        action="scraper_dataset_restore_replayed",
        target_type="dataset_publication",
        target_id=existing.id,
        actor_id="system:restore",
        reason="Validated restore replay",
        details={
            "before": None,
            "after": {
                "datasetVersion": restored_version,
                "syncStatus": sync_result["status"],
            },
            "checksum": restored_checksum,
        },
    )
    session.flush()
    return {
        **result,
        "applied": True,
        "syncStatus": sync_result["status"],
    }
