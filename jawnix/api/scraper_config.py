from __future__ import annotations

import uuid
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..activity import record_activity
from ..auth import Principal, require_admin
from ..database import get_db
from ..jobs import enqueue_job
from ..models import (
    ScrapeSegmentResult,
    ScraperConfiguration,
    ScraperRun,
    SourceSegment,
    utcnow,
)
from ..schemas import ActionReason
from fastapi import APIRouter


router = APIRouter()


def _configuration_dict(item: ScraperConfiguration) -> dict:
    return {
        "id": str(item.id),
        "version": item.version,
        "checksum": item.checksum,
        "status": item.status,
        "createdBy": str(item.created_by),
        "reason": item.reason,
        "anomalyThresholds": item.anomaly_thresholds,
        "createdAt": item.created_at,
        "scheduledAt": item.scheduled_at,
        "activatedAt": item.activated_at,
        "basedOnConfigurationId": (
            str(item.based_on_configuration_id)
            if item.based_on_configuration_id
            else None
        ),
        "segments": [
            {
                "key": segment.key,
                "niche": segment.niche,
                "query": segment.query,
                "geography": segment.geography,
                "parameters": segment.parameters,
            }
            for segment in item.segments
        ],
    }


@router.get("/api/admin/scraper-configurations")
def list_scraper_configurations(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [
        _configuration_dict(item)
        for item in db.scalars(
            select(ScraperConfiguration).order_by(
                ScraperConfiguration.version.desc()
            )
        )
    ]


@router.get("/api/admin/scraper-configurations/{configuration_id}")
def get_scraper_configuration(
    configuration_id: uuid.UUID,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(ScraperConfiguration, configuration_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Scraper Configuration was not found.",
        )
    return _configuration_dict(item)


@router.get("/api/admin/scrape-runs/{run_id}")
def get_scrape_run(
    run_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = db.get(ScraperRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="Scrape Run was not found.",
        )
    return {
        "id": run.id,
        "source": run.source,
        "sourceVersion": run.source_version,
        "configurationId": (
            str(run.configuration_id)
            if run.configuration_id is not None
            else None
        ),
        "datasetVersion": run.dataset_version,
        "manual": run.manual,
        "checksum": run.checksum,
        "status": run.status,
        "rowsSeen": run.rows_seen,
        "rowsImported": run.rows_imported,
        "details": run.details,
        "startedAt": run.started_at,
        "finishedAt": run.finished_at,
        "segments": [
            {
                "key": result.segment_key,
                "niche": result.niche,
                "geography": result.geography,
                "observed": result.observed_count,
                "valid": result.valid_count,
                "new": result.new_count,
                "duplicates": result.duplicate_count,
                "quarantined": result.quarantined_count,
                "anomalous": result.anomalous,
                "anomalyReasons": result.anomaly_reasons,
            }
            for result in db.scalars(
                select(ScrapeSegmentResult)
                .where(ScrapeSegmentResult.scraper_run_id == run.id)
                .order_by(ScrapeSegmentResult.segment_key)
            )
        ],
    }


@router.post(
    "/api/admin/scraper-configurations/{configuration_id}/schedule"
)
def schedule_scraper_configuration(
    configuration_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.scalar(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.id == configuration_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Scraper Configuration was not found.",
        )
    previous_status = item.status
    for scheduled in db.scalars(
        select(ScraperConfiguration)
        .where(
            ScraperConfiguration.status == "scheduled",
            ScraperConfiguration.id != item.id,
        )
        .with_for_update()
    ):
        scheduled.status = "schedule_replaced"
    item.status = "scheduled"
    item.scheduled_at = utcnow()
    record_activity(
        db,
        action="scraper_configuration_scheduled",
        target_type="scraper_configuration",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"status": previous_status},
            "after": {"status": "scheduled"},
            "version": item.version,
        },
    )
    db.commit()
    db.refresh(item)
    return _configuration_dict(item)


@router.post(
    "/api/admin/scraper-configurations/{configuration_id}/manual-run",
    status_code=202,
)
def queue_manual_scrape_run(
    configuration_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(ScraperConfiguration, configuration_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Scraper Configuration was not found.",
        )
    job_payload = {
        "configuration_id": str(item.id),
        "reason": payload.reason.strip(),
        "actor_user_id": str(principal.user_id),
        "manual": True,
    }
    enqueue_job(db, "run_scraper", payload=job_payload)
    record_activity(
        db,
        action="scraper_manual_run_queued",
        target_type="scraper_configuration",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": {"manualRunQueued": False},
            "after": {"manualRunQueued": True},
            "version": item.version,
        },
    )
    db.commit()
    return {
        "queued": True,
        "configurationId": str(item.id),
    }


@router.post(
    "/api/admin/scraper-configurations/{configuration_id}/rollback",
    status_code=201,
)
def rollback_scraper_configuration(
    configuration_id: uuid.UUID,
    payload: ActionReason,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    source = db.get(ScraperConfiguration, configuration_id)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail="Scraper Configuration was not found.",
        )
    latest = db.scalar(
        select(ScraperConfiguration)
        .order_by(ScraperConfiguration.version.desc())
        .limit(1)
        .with_for_update()
    )
    for scheduled in db.scalars(
        select(ScraperConfiguration)
        .where(ScraperConfiguration.status == "scheduled")
        .with_for_update()
    ):
        scheduled.status = "schedule_replaced"
    item = ScraperConfiguration(
        version=(latest.version if latest else 0) + 1,
        checksum=source.checksum,
        status="scheduled",
        anomaly_thresholds=source.anomaly_thresholds,
        created_by=principal.user_id,
        reason=payload.reason.strip(),
        scheduled_at=utcnow(),
        based_on_configuration_id=source.id,
        segments=[
            SourceSegment(
                key=segment.key,
                niche=segment.niche,
                query=segment.query,
                geography=segment.geography,
                parameters=segment.parameters,
            )
            for segment in source.segments
        ],
    )
    db.add(item)
    db.flush()
    record_activity(
        db,
        action="scraper_configuration_rollback_scheduled",
        target_type="scraper_configuration",
        target_id=item.id,
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "before": None,
            "after": {"status": "scheduled"},
            "version": item.version,
            "basedOnConfigurationId": str(source.id),
        },
    )
    db.commit()
    db.refresh(item)
    return _configuration_dict(item)
