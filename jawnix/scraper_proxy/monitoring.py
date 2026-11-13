from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..scraper_monitoring import (
    ControlPipelineRequest,
    REGION_INTERVALS,
    REGIONS,
    MonitoringRegion,
    MonitoringSnapshot,
    RegionKey,
    monitoring_region,
    region_data,
    snapshot_regions,
)
from ..scraper_operations import ScraperOperationsError
from .core import (
    _mark_operations_success,
    _native_unavailable,
    _scraper_operations,
    _state,
    _workspace_now,
    native_router,
)
from .grants import _require_scraper_grant


class PipelineControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume"]
    # Only meaningful while pausing, and the whole difference between the two
    # pauses the dashboard offers: draining stops the refill and lets running
    # jobs finish, clearing also cancels every job already queued. Defaulting
    # to False keeps the destructive one an explicit choice.
    clear_queue: bool = False
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def only_a_pause_clears_the_queue(self):
        if self.clear_queue and self.action != "pause":
            raise ValueError("Only a pause can clear the queue.")
        return self


class PipelineResult(BaseModel):
    """The write's outcome plus the activity region it just changed.

    Returning the region means the screen shows the new pipeline state without
    waiting for its next poll, which for a destructive action is the difference
    between confirming what happened and hoping.
    """

    ok: bool
    pipeline_state: str
    cancelled_jobs: int
    region: MonitoringRegion


def _unavailable_region(region: str) -> MonitoringRegion:
    return MonitoringRegion(
        region=region,
        state="unavailable",
        refresh_seconds=REGION_INTERVALS[region],
    )


@native_router.get("/monitoring", response_model=MonitoringSnapshot)
async def scraper_monitoring(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bootstrap: bool = False,
):
    """Every monitoring region at once, for the workspace's first paint.

    An outage answers 200 with `service_state: unavailable` rather than an
    error status. The Jawnix request did succeed; what it reports is the
    Scraper's health, and a client that receives a well-formed snapshot can
    show the outage while keeping whatever it already had on screen.
    """

    _require_scraper_grant(request, response, principal, settings)
    state = _state(request, settings)
    if bootstrap:
        operations = _scraper_operations(request, settings)

        async def initial_region(key):
            try:
                payload = await operations.monitoring_region(key)
            except ScraperOperationsError:
                return _unavailable_region(key)
            return monitoring_region(
                key, payload.model_dump(mode="json", exclude_none=True),
                fetched_at=_workspace_now(request),
            )

        initial = await asyncio.gather(initial_region("overall"), initial_region("stats"))
        connected = any(region.data is not None for region in initial)
        if connected:
            _mark_operations_success(request, settings)
        ready = {region.region: region for region in initial}
        return MonitoringSnapshot(
            service_state="connected" if connected else "unavailable",
            last_successful_at=state.last_success,
            regions=[
                ready.get(region) or MonitoringRegion(
                    region=region, state="loading" if connected else "unavailable",
                    refresh_seconds=REGION_INTERVALS[region],
                )
                for region in REGIONS
            ],
        )
    try:
        payload = await _scraper_operations(
            request,
            settings,
        ).monitoring_dashboard()
    except ScraperOperationsError:
        return MonitoringSnapshot(
            service_state="unavailable",
            last_successful_at=state.last_success,
            regions=[_unavailable_region(region) for region in REGIONS],
        )
    _mark_operations_success(request, settings)
    return MonitoringSnapshot(
        service_state="connected",
        last_successful_at=state.last_success,
        regions=snapshot_regions(
            payload.model_dump(mode="json", exclude_none=True),
            fetched_at=_workspace_now(request),
        ),
    )


@native_router.get(
    "/monitoring/{region}",
    response_model=MonitoringRegion,
)
async def scraper_monitoring_region(
    region: RegionKey,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """One region, so a screen can refresh each at its own cadence.

    Reading regions separately is what preserves the dashboard's failure
    isolation: this call failing says nothing about the other eight.
    """

    _require_scraper_grant(request, response, principal, settings)
    try:
        payload = await _scraper_operations(
            request,
            settings,
        ).monitoring_region(region)
    except ScraperOperationsError:
        return _unavailable_region(region)
    _mark_operations_success(request, settings)
    return monitoring_region(
        region, payload.model_dump(mode="json", exclude_none=True),
        fetched_at=_workspace_now(request),
    )


@native_router.post("/pipeline", response_model=PipelineResult)
async def control_scraper_pipeline(
    payload: PipelineControl,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Pause, pause-and-clear, or resume the acquisition pipeline.

    The write goes to the same upstream control the dashboard has always
    posted to, so this is not a second way to change the pipeline. What Jawnix
    adds around it is the privileged session, the required reason, and a
    durable audit entry that distinguishes the destructive pause from the
    ordinary one.
    """

    _require_scraper_grant(request, response, principal, settings)
    state = _state(request, settings)
    try:
        result = await _scraper_operations(
            request,
            settings,
        ).control_pipeline(
            ControlPipelineRequest(
                action=payload.action,
                clear_queue=payload.clear_queue,
            )
        )
    except ScraperOperationsError:
        return _native_unavailable(state)
    _mark_operations_success(request, settings)
    region = MonitoringRegion(
        region="activity",
        state="ok",
        refresh_seconds=REGION_INTERVALS["activity"],
        fetched_at=_workspace_now(request),
        data=region_data(
            "activity",
            {
                "activity": result.activity.model_dump(mode="json"),
                "pipeline_state": result.pipeline_state.model_dump(
                    mode="json"
                ),
                "pause_info": result.pause_info.model_dump(mode="json"),
            },
        ),
    )
    pipeline_state = result.pipeline_state.key
    cancelled_jobs = result.cancelled_jobs

    if payload.action == "resume":
        action = "scraper_pipeline_resumed"
    elif payload.clear_queue:
        action = "scraper_pipeline_queue_cleared"
    else:
        action = "scraper_pipeline_paused"
    record_activity(
        db,
        action=action,
        target_type="scraper_pipeline",
        target_id="primary",
        actor_id=principal.user_id,
        reason=payload.reason,
        details={
            "pipelineState": pipeline_state,
            "clearedQueue": payload.clear_queue,
            "cancelledJobs": cancelled_jobs,
        },
    )
    db.commit()
    return PipelineResult(
        ok=True,
        pipeline_state=pipeline_state,
        cancelled_jobs=cancelled_jobs,
        region=region,
    )
