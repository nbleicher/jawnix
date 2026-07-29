from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import statistics
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jawnix.activity import record_activity
from jawnix.config import Settings
from jawnix.jobs import enqueue_job
from jawnix.models import (
    DatasetPublication,
    InventoryConflict,
    InventorySyncAttempt,
    Lead,
    LeadRequest,
    NightlyReview,
    RequestStatus,
    ScraperRun,
    ScraperConfiguration,
    ScrapeAnomaly,
    ScrapeSegmentResult,
    SourceRecommendation,
)
from jawnix.states import US_STATES, normalize_phone

from .migration import import_scraper_sqlite


_SCRAPER_DATASET_LOCK_NAMESPACE = 0x4A41574E
_SCRAPER_DATASET_LOCK_RESOURCE = 0x49584442


def lock_scraper_dataset(session: Session) -> None:
    """Serialize every PostgreSQL transaction that can replace the dataset."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                _SCRAPER_DATASET_LOCK_NAMESPACE,
                _SCRAPER_DATASET_LOCK_RESOURCE,
            )
        )
    ).one()


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _analyze_segments(
    session: Session,
    path: Path,
    configuration: ScraperConfiguration,
) -> tuple[list[ScrapeSegmentResult], list[str]]:
    results: list[ScrapeSegmentResult] = []
    anomalous_segments: list[str] = []
    with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as source:
        source.row_factory = sqlite3.Row
        columns = {
            str(row["name"])
            for row in source.execute("PRAGMA table_info(leads)")
        }
        required = {
            "phone",
            "company",
            "full_name",
            "state",
            "source",
        }
        missing = sorted(required - columns)
        if missing:
            raise ValueError(
                "Google Maps staged dataset is missing column(s): "
                + ", ".join(missing)
            )
        for segment in configuration.segments:
            rows = list(
                source.execute(
                    """
                    SELECT phone, company, full_name, state
                    FROM leads
                    WHERE source = ?
                    """,
                    (segment.key,),
                )
            )
            valid_phones = []
            for row in rows:
                phone = normalize_phone(row["phone"])
                title = str(row["company"] or row["full_name"] or "").strip()
                state = str(row["state"] or "").strip().upper()
                if phone and title and state in US_STATES:
                    valid_phones.append(phone)
            unique_phones = set(valid_phones)
            existing_phones = (
                {
                    phone
                    for phone in session.scalars(
                        select(Lead.phone).where(
                            Lead.phone.in_(unique_phones)
                        )
                    )
                }
                if unique_phones
                else set()
            )
            history_runs = int(
                configuration.anomaly_thresholds.get("history_runs", 7)
            )
            history = list(
                session.scalars(
                    select(ScrapeSegmentResult.valid_count)
                    .join(
                        ScraperRun,
                        ScraperRun.id
                        == ScrapeSegmentResult.scraper_run_id,
                    )
                    .where(
                        ScrapeSegmentResult.segment_key == segment.key,
                        ScraperRun.status == "complete",
                    )
                    .order_by(ScraperRun.finished_at.desc(), ScraperRun.id.desc())
                    .limit(history_runs)
                )
            )
            reasons = []
            valid_count = len(valid_phones)
            if valid_count == 0:
                reasons.append("zero_valid_listings")
            elif len(history) >= history_runs:
                median = statistics.median(history)
                down_fraction = float(
                    configuration.anomaly_thresholds.get(
                        "down_fraction",
                        0.5,
                    )
                )
                up_multiplier = float(
                    configuration.anomaly_thresholds.get(
                        "up_multiplier",
                        2.0,
                    )
                )
                if valid_count < median * (1 - down_fraction):
                    reasons.append("more_than_50_percent_down")
                if valid_count > median * up_multiplier:
                    reasons.append("more_than_200_percent_up")
            result = ScrapeSegmentResult(
                scraper_run_id=0,
                segment_key=segment.key,
                niche=segment.niche,
                geography=segment.geography,
                observed_count=len(rows),
                valid_count=valid_count,
                new_count=len(unique_phones - existing_phones),
                duplicate_count=len(valid_phones) - len(unique_phones),
                quarantined_count=len(rows) - valid_count,
                anomalous=bool(reasons),
                anomaly_reasons=reasons,
            )
            results.append(result)
            if reasons:
                anomalous_segments.append(segment.key)
    return results, anomalous_segments


def _publish_staged_dataset(
    session: Session,
    settings: Settings,
    run: ScraperRun,
    configuration_id: uuid.UUID,
    staged_path: Path,
    checksum: str,
) -> tuple[DatasetPublication, bool]:
    existing = session.scalar(
        select(DatasetPublication).where(
            DatasetPublication.checksum == checksum
        )
    )
    if existing is not None:
        staged_path.unlink(missing_ok=True)
        return existing, False
    version = int(
        session.scalar(select(func.max(DatasetPublication.version))) or 0
    ) + 1
    active_path = Path(settings.scraper_db_path)
    versions_directory = active_path.parent / ".versions"
    versions_directory.mkdir(parents=True, exist_ok=True)
    archived_path = (
        versions_directory / f"{version:020d}-{checksum}.db"
    )
    shutil.copy2(staged_path, archived_path)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged_path, active_path)
    publication = DatasetPublication(
        version=version,
        checksum=checksum,
        scraper_run_id=run.id,
        configuration_id=configuration_id,
        storage_path=str(archived_path),
        sync_status="pending",
    )
    session.add(publication)
    session.flush()
    configuration = session.get(
        ScraperConfiguration,
        configuration_id,
    )
    metadata = {
        "schemaVersion": 1,
        "datasetVersion": version,
        "checksum": checksum,
        "configuration": {
            "id": str(configuration.id),
            "version": configuration.version,
            "checksum": configuration.checksum,
            "anomalyThresholds": configuration.anomaly_thresholds,
            "reason": configuration.reason,
            "createdBy": str(configuration.created_by),
            "segments": [
                {
                    "key": segment.key,
                    "niche": segment.niche,
                    "query": segment.query,
                    "geography": segment.geography,
                    "parameters": segment.parameters,
                }
                for segment in configuration.segments
            ],
        },
    }
    metadata_path = active_path.parent / "dataset-metadata.json"
    metadata_staged_path = (
        active_path.parent / ".dataset-metadata.json.tmp"
    )
    metadata_staged_path.write_text(
        json.dumps(metadata, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(metadata_staged_path, metadata_path)
    return publication, True


def sync_dataset_version(
    session: Session,
    settings: Settings,
    dataset_version: int,
    force: bool = False,
) -> dict:
    publication = session.scalar(
        select(DatasetPublication)
        .where(DatasetPublication.version == dataset_version)
        .with_for_update()
    )
    if publication is None:
        raise LookupError("Scraper Dataset version was not found.")
    if publication.sync_status == "complete" and not force:
        return {
            "status": "complete",
            "datasetVersion": publication.version,
            "checksum": publication.checksum,
            "duplicate": True,
        }
    attempt_number = int(
        session.scalar(
            select(func.max(InventorySyncAttempt.attempt_number)).where(
                InventorySyncAttempt.dataset_publication_id
                == publication.id
            )
        )
        or 0
    ) + 1
    attempt = InventorySyncAttempt(
        dataset_publication_id=publication.id,
        dataset_version=publication.version,
        dataset_checksum=publication.checksum,
        attempt_number=attempt_number,
        status="running",
    )
    session.add(attempt)
    session.flush()
    dataset_path = Path(publication.storage_path)
    if not dataset_path.is_file():
        dataset_path = Path(settings.scraper_db_path)
    try:
        if _file_checksum(dataset_path) != publication.checksum:
            raise RuntimeError(
                "Scraper Dataset checksum does not match its publication."
            )
        with session.begin_nested():
            result = import_scraper_sqlite(
                session,
                dataset_path,
                expected_checksum=publication.checksum,
            )
        attempt.status = "complete"
        attempt.result = result
        attempt.finished_at = datetime.now(timezone.utc)
        publication.sync_status = "complete"
        publication.synchronized_at = attempt.finished_at
        enqueue_job(session, "fulfill_round_robin")
        run = session.get(ScraperRun, publication.scraper_run_id)
        if run is not None:
            run.status = "complete"
            run.rows_seen = int(result.get("sourceRows", 0))
            run.rows_imported = int(result.get("imported", 0))
            run.details = {**run.details, **result}
            run.finished_at = attempt.finished_at
            _update_nightly_review_after_sync(
                session,
                run,
                publication,
            )
        session.flush()
        return {
            **result,
            "status": "complete",
            "datasetVersion": publication.version,
            "checksum": publication.checksum,
            "syncAttempt": attempt.attempt_number,
        }
    except Exception as exc:
        attempt.status = "failed"
        attempt.error = str(exc)
        attempt.finished_at = datetime.now(timezone.utc)
        publication.sync_status = "failed"
        run = session.get(ScraperRun, publication.scraper_run_id)
        if run is not None:
            run.status = "sync_failed"
            run.details = {
                **run.details,
                "inventorySyncError": str(exc),
            }
            run.finished_at = attempt.finished_at
            _update_nightly_review_after_sync(
                session,
                run,
                publication,
            )
        enqueue_job(
            session,
            "sync_inventory",
            payload={"dataset_version": publication.version},
        )
        session.flush()
        return {
            "status": "sync_failed",
            "datasetVersion": publication.version,
            "checksum": publication.checksum,
            "syncAttempt": attempt.attempt_number,
            "error": str(exc),
        }


def _nightly_operational_snapshot(session: Session) -> dict:
    inventory_by_state = {
        state: int(count)
        for state, count in session.execute(
            select(Lead.state, func.count(Lead.id))
            .group_by(Lead.state)
            .order_by(Lead.state)
        )
    }
    return {
        "inventory": {
            "total": int(
                session.scalar(select(func.count(Lead.id))) or 0
            ),
            "byState": inventory_by_state,
        },
        "waitingRequests": [
            {
                "id": str(item.id),
                "customerId": item.customer_id,
                "requested": item.lead_count,
                "available": item.available_count,
                "states": item.states_snapshot,
            }
            for item in session.scalars(
                select(LeadRequest)
                .where(
                    LeadRequest.status
                    == RequestStatus.waiting_inventory.value
                )
                .order_by(LeadRequest.created_at, LeadRequest.id)
            )
        ],
        "inventoryConflicts": [
            {
                "id": str(item.id),
                "status": item.status,
                "olderRequestId": str(item.older_request_id),
                "newerRequestId": str(item.newer_request_id),
            }
            for item in session.scalars(
                select(InventoryConflict)
                .where(
                    InventoryConflict.status.in_(
                        {"pending", "confirmed", "denied"}
                    )
                )
                .order_by(InventoryConflict.created_at)
            )
        ],
        "recommendations": [
            {
                "id": str(item.id),
                "niche": item.niche,
                "segment": item.segment_key,
                "action": item.action,
                "status": item.status,
            }
            for item in session.scalars(
                select(SourceRecommendation)
                .where(SourceRecommendation.status == "pending")
                .order_by(SourceRecommendation.created_at)
            )
        ],
    }


def _update_nightly_review_after_sync(
    session: Session,
    run: ScraperRun,
    publication: DatasetPublication,
) -> None:
    review = session.scalar(
        select(NightlyReview).where(
            NightlyReview.scraper_run_id == run.id
        )
    )
    if review is None:
        return
    summary = dict(review.summary)
    summary["run"] = {
        **(summary.get("run") or {}),
        "status": run.status,
    }
    summary["dataset"] = {
        **(summary.get("dataset") or {}),
        "version": publication.version,
        "checksum": publication.checksum,
        "syncStatus": publication.sync_status,
    }
    retained_failures = [
        item
        for item in (summary.get("failures") or [])
        if item.get("kind") != "inventory_sync"
    ]
    summary["failures"] = retained_failures + [
        {
            "kind": "inventory_sync",
            "attempt": item.attempt_number,
            "message": item.error,
        }
        for item in session.scalars(
            select(InventorySyncAttempt)
            .where(
                InventorySyncAttempt.dataset_publication_id
                == publication.id,
                InventorySyncAttempt.status == "failed",
            )
            .order_by(InventorySyncAttempt.attempt_number)
        )
    ]
    summary.update(_nightly_operational_snapshot(session))
    review.summary = summary
    review.status = (
        "complete" if publication.sync_status == "complete" else "attention"
    )
    if review.telegram_message_id:
        enqueue_job(
            session,
            "update_nightly_review_notification",
            payload={"review_id": str(review.id)},
        )


def create_nightly_review(
    session: Session,
    run: ScraperRun,
) -> NightlyReview:
    existing = session.scalar(
        select(NightlyReview).where(
            NightlyReview.scraper_run_id == run.id
        )
    )
    if existing is not None:
        return existing
    configuration = (
        session.get(ScraperConfiguration, run.configuration_id)
        if run.configuration_id is not None
        else None
    )
    publication = session.scalar(
        select(DatasetPublication).where(
            DatasetPublication.scraper_run_id == run.id
        )
    )
    segment_rows = list(
        session.scalars(
            select(ScrapeSegmentResult)
            .where(ScrapeSegmentResult.scraper_run_id == run.id)
            .order_by(ScrapeSegmentResult.segment_key)
        )
    )
    anomaly = session.scalar(
        select(ScrapeAnomaly).where(
            ScrapeAnomaly.scraper_run_id == run.id
        )
    )
    operational_snapshot = _nightly_operational_snapshot(session)
    failures = []
    if run.status in {"failed", "sync_failed"}:
        failures.append(
            {
                "kind": "scrape_run",
                "message": (
                    run.details.get("error")
                    or run.details.get("inventorySyncError")
                    or run.status
                ),
            }
        )
    if run.details.get("recommendationError"):
        failures.append(
            {
                "kind": "source_recommendations",
                "message": run.details["recommendationError"],
            }
        )
    if publication is not None:
        failures.extend(
            {
                "kind": "inventory_sync",
                "attempt": attempt.attempt_number,
                "message": attempt.error,
            }
            for attempt in session.scalars(
                select(InventorySyncAttempt)
                .where(
                    InventorySyncAttempt.dataset_publication_id
                    == publication.id,
                    InventorySyncAttempt.status == "failed",
                )
                .order_by(InventorySyncAttempt.attempt_number)
            )
        )
    review = NightlyReview(
        scraper_run_id=run.id,
        status=(
            "complete"
            if run.status in {"complete", "skipped_duplicate"}
            else "attention"
        ),
        summary={
            "configuration": {
                "id": (
                    str(configuration.id)
                    if configuration is not None
                    else None
                ),
                "version": (
                    configuration.version
                    if configuration is not None
                    else None
                ),
            },
            "run": {
                "id": run.id,
                "status": run.status,
                "manual": run.manual,
                "anomalyId": (
                    str(anomaly.id) if anomaly is not None else None
                ),
                "anomalyStatus": (
                    anomaly.status if anomaly is not None else None
                ),
            },
            "dataset": {
                "version": (
                    publication.version
                    if publication is not None
                    else run.dataset_version
                ),
                "checksum": (
                    publication.checksum
                    if publication is not None
                    else run.checksum
                ),
                "syncStatus": (
                    publication.sync_status
                    if publication is not None
                    else None
                ),
            },
            "segments": [
                {
                    "key": item.segment_key,
                    "niche": item.niche,
                    "geography": item.geography,
                    "observed": item.observed_count,
                    "valid": item.valid_count,
                    "new": item.new_count,
                    "duplicate": item.duplicate_count,
                    "quarantined": item.quarantined_count,
                    "anomalous": item.anomalous,
                    "anomalyReasons": item.anomaly_reasons,
                }
                for item in segment_rows
            ],
            **operational_snapshot,
            "failures": failures,
        },
    )
    session.add(review)
    session.flush()
    enqueue_job(
        session,
        "notify_nightly_review",
        payload={"review_id": str(review.id)},
    )
    return review


def run_nightly_attempt(
    session: Session,
    settings: Settings,
) -> NightlyReview:
    lock_scraper_dataset(session)
    scheduled = session.scalar(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.status == "scheduled")
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
        .with_for_update()
    )
    if scheduled is not None:
        for active in session.scalars(
            select(ScraperConfiguration)
            .where(ScraperConfiguration.status == "active")
            .with_for_update()
        ):
            active.status = "superseded"
        scheduled.status = "active"
        scheduled.activated_at = datetime.now(timezone.utc)
    configuration = scheduled or session.scalar(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.status == "active")
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
    )
    if configuration is None:
        run = ScraperRun(
            source="google_maps",
            source_version="no-active-configuration",
            status="failed",
            details={"error": "No active Scraper Configuration."},
            finished_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.flush()
    else:
        result = run_scrape(
            session,
            settings,
            configuration.id,
            manual=False,
            nightly=True,
        )
        run = session.get(ScraperRun, int(result["runId"]))
    from jawnix.performance import build_source_recommendations

    try:
        with session.begin_nested():
            build_source_recommendations(session)
    except Exception as exc:
        run.details = {
            **run.details,
            "recommendationError": str(exc),
        }
    return create_nightly_review(session, run)


def run_scrape(
    session: Session,
    settings: Settings,
    configuration_id: uuid.UUID,
    manual: bool = False,
    nightly: bool = False,
) -> dict:
    lock_scraper_dataset(session)
    configuration = session.scalar(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.id == configuration_id)
        .with_for_update()
    )
    if configuration is None:
        raise LookupError("Scraper Configuration was not found.")
    active_path = Path(settings.scraper_db_path)
    staging_directory = active_path.parent / ".staging"
    staging_directory.mkdir(parents=True, exist_ok=True)
    staged_path = staging_directory / f"{uuid.uuid4()}.db"
    if active_path.is_file():
        shutil.copy2(active_path, staged_path)
    run = ScraperRun(
        source="google_maps",
        source_version=f"staging:{staged_path.stem}",
        configuration_id=configuration.id,
        staged_path=str(staged_path),
        manual=manual,
        status="running",
    )
    session.add(run)
    session.flush()
    try:
        if not settings.scraper_command:
            raise RuntimeError("JAWNIX_SCRAPER_COMMAND is not configured.")
        environment = os.environ.copy()
        environment["JAWNIX_SCRAPER_DB_PATH"] = str(staged_path)
        environment["JAWNIX_SCRAPER_CONFIGURATION"] = json.dumps(
            {
                "id": str(configuration.id),
                "version": configuration.version,
                "checksum": configuration.checksum,
                "anomaly_thresholds": configuration.anomaly_thresholds,
                "segments": [
                    {
                        "key": segment.key,
                        "niche": segment.niche,
                        "query": segment.query,
                        "geography": segment.geography,
                        "parameters": segment.parameters,
                    }
                    for segment in configuration.segments
                ],
            },
            sort_keys=True,
        )
        subprocess.run(
            shlex.split(settings.scraper_command),
            check=True,
            env=environment,
        )
        if not staged_path.is_file():
            raise RuntimeError(
                "Google Maps Scraper did not produce the staged dataset."
            )
        checksum = _file_checksum(staged_path)
        segment_results, anomalous_segments = _analyze_segments(
            session,
            staged_path,
            configuration,
        )
        for segment_result in segment_results:
            segment_result.scraper_run_id = run.id
            session.add(segment_result)
        if anomalous_segments:
            run.source_version = checksum
            run.checksum = checksum
            run.status = "held_anomaly"
            run.details = {
                "anomalousSegments": anomalous_segments,
                "stagedChecksum": checksum,
            }
            run.finished_at = datetime.now(timezone.utc)
            anomaly = ScrapeAnomaly(
                scraper_run_id=run.id,
                configuration_id=configuration.id,
                dataset_checksum=checksum,
                status="pending",
            )
            session.add(anomaly)
            session.flush()
            if not nightly:
                enqueue_job(
                    session,
                    "notify_scrape_anomaly",
                    payload={"anomaly_id": str(anomaly.id)},
                )
            return {
                "status": run.status,
                "runId": run.id,
                "checksum": checksum,
                "anomalousSegments": anomalous_segments,
            }
        run.source_version = checksum
        run.checksum = checksum
        publication, published = _publish_staged_dataset(
            session,
            settings,
            run,
            configuration.id,
            staged_path,
            checksum,
        )
        run.dataset_version = publication.version
        run.staged_path = ""
        if not published:
            run.status = "skipped_duplicate"
            run.details = {
                "reason": "dataset checksum already published",
                "existingDatasetVersion": publication.version,
            }
            run.finished_at = datetime.now(timezone.utc)
            session.flush()
            return {
                "status": run.status,
                "runId": run.id,
                "datasetVersion": publication.version,
                "checksum": checksum,
                "skipped": True,
            }
        run.status = "published"
        enqueue_job(
            session,
            "sync_inventory",
            payload={"dataset_version": publication.version},
        )
        session.flush()
        return {
            "status": "published",
            "runId": run.id,
            "datasetVersion": publication.version,
            "checksum": checksum,
            "syncStatus": "pending",
        }
    except Exception as exc:
        staged_path.unlink(missing_ok=True)
        run.status = "failed"
        run.details = {"error": str(exc)}
        run.finished_at = datetime.now(timezone.utc)
        run.staged_path = ""
        session.flush()
        return {
            "status": "failed",
            "runId": run.id,
            "error": str(exc),
        }


def _enqueue_scrape_anomaly_notification_refresh(
    session: Session,
    anomaly: ScrapeAnomaly,
    *,
    nightly_review: NightlyReview | None = None,
) -> None:
    """Make Telegram project the Scrape Anomaly's current durable state.

    Successful decisions already use this refresh path. Duplicates and
    cross-channel races must use it too, or a callback that safely does
    nothing in the database can leave an old inline keyboard looking live.
    """
    if nightly_review is None:
        nightly_review = session.scalar(
            select(NightlyReview).where(
                NightlyReview.scraper_run_id
                == anomaly.scraper_run_id
            )
        )
    if nightly_review is not None:
        enqueue_job(
            session,
            "update_nightly_review_notification",
            payload={"review_id": str(nightly_review.id)},
        )
        return
    enqueue_job(
        session,
        "update_scrape_anomaly_notification",
        payload={"anomaly_id": str(anomaly.id)},
    )


def decide_scrape_anomaly(
    session: Session,
    settings: Settings,
    anomaly_id: uuid.UUID,
    action: str,
    actor_id: str,
    reason: str,
) -> dict:
    if action not in {"confirm", "deny"}:
        raise ValueError("Scrape Anomaly action must be confirm or deny.")
    lock_scraper_dataset(session)
    anomaly = session.scalar(
        select(ScrapeAnomaly)
        .where(ScrapeAnomaly.id == anomaly_id)
        .with_for_update()
    )
    if anomaly is None:
        raise LookupError("Scrape Anomaly was not found.")
    if anomaly.status != "pending":
        _enqueue_scrape_anomaly_notification_refresh(session, anomaly)
        return {
            "status": anomaly.status,
            "duplicate": True,
            "anomalyId": str(anomaly.id),
        }
    run = session.scalar(
        select(ScraperRun)
        .where(ScraperRun.id == anomaly.scraper_run_id)
        .with_for_update()
    )
    if run is None or run.status != "held_anomaly":
        raise RuntimeError("Held Scrape Run was not found.")
    configuration = session.scalar(
        select(ScraperConfiguration)
        .where(
            ScraperConfiguration.id == anomaly.configuration_id
        )
        .with_for_update()
    )
    if configuration is None:
        raise RuntimeError("Scraper Configuration was not found.")
    staged_path = Path(run.staged_path).resolve()
    staging_root = (
        Path(settings.scraper_db_path).parent / ".staging"
    ).resolve()
    if not staged_path.is_relative_to(staging_root) or not staged_path.is_file():
        raise RuntimeError("Held staged dataset is missing or outside staging.")
    checksum = _file_checksum(staged_path)
    if checksum != anomaly.dataset_checksum:
        raise RuntimeError("Held staged dataset checksum changed.")

    now = datetime.now(timezone.utc)
    newer_run = session.scalar(
        select(ScraperRun)
        .where(
            ScraperRun.id > run.id,
            ScraperRun.source == run.source,
            ScraperRun.status.in_(
                {
                    "held_anomaly",
                    "published",
                    "skipped_duplicate",
                    "complete",
                    "sync_failed",
                }
            ),
        )
        .order_by(ScraperRun.id.desc())
        .limit(1)
        .with_for_update()
    )
    if newer_run is not None:
        staged_path.unlink()
        anomaly.status = "superseded"
        anomaly.decision_by = actor_id
        anomaly.decision_reason = reason.strip()
        anomaly.decided_at = now
        run.status = "superseded"
        run.staged_path = ""
        run.details = {
            **run.details,
            "anomalyDecision": "superseded",
            "requestedAction": action,
            "newerScraperRunId": newer_run.id,
        }
        run.finished_at = now
        record_activity(
            session,
            action="scrape_anomaly_superseded",
            target_type="scrape_anomaly",
            target_id=anomaly.id,
            actor_id=actor_id,
            reason=reason,
            details={
                "before": {"status": "pending"},
                "after": {"status": "superseded"},
                "scraperRunId": run.id,
                "datasetChecksum": checksum,
                "requestedAction": action,
                "newerScraperRunId": newer_run.id,
            },
        )
        nightly_review = session.scalar(
            select(NightlyReview).where(
                NightlyReview.scraper_run_id == run.id
            )
        )
        if nightly_review is not None:
            nightly_review.summary = {
                **nightly_review.summary,
                "run": {
                    **(nightly_review.summary.get("run") or {}),
                    "status": run.status,
                    "anomalyStatus": anomaly.status,
                },
            }
        _enqueue_scrape_anomaly_notification_refresh(
            session,
            anomaly,
            nightly_review=nightly_review,
        )
        session.flush()
        return {
            "status": anomaly.status,
            "anomalyId": str(anomaly.id),
            "newerScraperRunId": newer_run.id,
        }

    anomaly.status = "confirmed" if action == "confirm" else "denied"
    anomaly.decision_by = actor_id
    anomaly.decision_reason = reason.strip()
    anomaly.decided_at = now
    result: dict = {
        "status": anomaly.status,
        "anomalyId": str(anomaly.id),
    }
    if action == "confirm":
        publication, published = _publish_staged_dataset(
            session,
            settings,
            run,
            anomaly.configuration_id,
            staged_path,
            checksum,
        )
        run.dataset_version = publication.version
        run.status = "published" if published else "skipped_duplicate"
        run.details = {
            **run.details,
            "anomalyDecision": "confirmed",
        }
        run.staged_path = ""
        run.finished_at = now
        if published:
            enqueue_job(
                session,
                "sync_inventory",
                payload={"dataset_version": publication.version},
            )
        result["syncStatus"] = (
            "pending" if published else publication.sync_status
        )
        result["datasetVersion"] = publication.version
        result["checksum"] = checksum
    else:
        staged_path.unlink()
        run.status = "denied"
        run.staged_path = ""
        run.details = {
            **run.details,
            "anomalyDecision": "denied",
        }
        run.finished_at = now
    record_activity(
        session,
        action=f"scrape_anomaly_{anomaly.status}",
        target_type="scrape_anomaly",
        target_id=anomaly.id,
        actor_id=actor_id,
        reason=reason,
        details={
            "before": {"status": "pending"},
            "after": {"status": anomaly.status},
            "scraperRunId": run.id,
            "datasetChecksum": checksum,
        },
    )
    nightly_review = session.scalar(
        select(NightlyReview).where(
            NightlyReview.scraper_run_id == run.id
        )
    )
    if nightly_review is not None:
        nightly_review.summary = {
            **nightly_review.summary,
            "run": {
                **(nightly_review.summary.get("run") or {}),
                "status": run.status,
                "anomalyStatus": anomaly.status,
            },
            "dataset": {
                **(nightly_review.summary.get("dataset") or {}),
                "version": run.dataset_version,
                "checksum": run.checksum,
                "syncStatus": (
                    "pending" if action == "confirm" else None
                ),
            },
        }
    _enqueue_scrape_anomaly_notification_refresh(
        session,
        anomaly,
        nightly_review=nightly_review,
    )
    session.flush()
    return result


def sync_scraper(
    session: Session,
    settings: Settings,
    source: str | None = None,
    force: bool = False,
) -> dict:
    """Synchronize the current committed Scraper Dataset into PostgreSQL."""
    if source and source.lower().replace("-", "_") not in {
        "gmaps",
        "google_maps",
    }:
        raise ValueError(
            "All future acquisition must use the Google Maps Scraper."
        )
    lock_scraper_dataset(session)
    publication = session.scalar(
        select(DatasetPublication)
        .order_by(DatasetPublication.version.desc())
        .limit(1)
    )
    if publication is None:
        raise LookupError(
            "No committed Scraper Dataset publication is available."
        )
    return sync_dataset_version(
        session,
        settings,
        publication.version,
        force=force,
    )
