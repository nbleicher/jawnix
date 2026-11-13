from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..models import ScraperRuntimeConfigurationRevision
from ..scraper_operations import ScraperOperationsError
from ..scraper_runtime import (
    CampaignHistory,
    CampaignHistoryRow,
    ControlRuntimeSaveRequest,
    ControlRuntimeWorkspace,
    HistorySort,
    RuntimeConfiguration,
    RuntimePreview,
    RuntimePreviewRequest,
    RuntimeSaveRequest,
    RuntimeSaveResult,
    RuntimeWorkspace,
    SortDirection,
    runtime_summary,
    runtime_version,
)
from ..states import US_STATES
from .core import (
    REVIEW_MAX_AGE_SECONDS,
    RUNTIME_CONFIGURATION_WRITE_LOCK,
    _mark_operations_success,
    _native_unavailable,
    _runtime_review_serializer,
    _safe_last_success,
    _scraper_operations,
    _state,
    _workspace_now,
    native_router,
)
from .grants import _require_scraper_grant


async def _runtime_current(
    request: Request,
    settings: Settings,
) -> ControlRuntimeWorkspace | None:
    try:
        workspace = await _scraper_operations(
            request,
            settings,
        ).runtime_workspace()
    except ScraperOperationsError:
        return None
    _mark_operations_success(request, settings)
    return workspace


def _audit_runtime_failure(
    db: Session,
    *,
    principal: Principal,
    action: str,
    reason: str,
    details: dict,
) -> None:
    record_activity(
        db,
        action=action,
        target_type="scraper_runtime_configuration",
        target_id="primary",
        actor_id=principal.user_id,
        reason=reason,
        details=details,
    )
    db.commit()


def _campaign_history_unavailable(
    request: Request,
    settings: Settings,
    *,
    search: str,
    state: str,
    sort: HistorySort,
    direction: SortDirection,
) -> CampaignHistory:
    proxy_state = _state(request, settings)
    return CampaignHistory(
        service_state="unavailable",
        last_successful_at=_safe_last_success(proxy_state),
        search=search,
        state=state,
        sort=sort,
        direction=direction,
        all_states=sorted(US_STATES),
        rows=[],
    )


@native_router.get("/history", response_model=CampaignHistory)
async def scraper_campaign_history(
    request: Request,
    response: Response,
    search: str = Query(default="", max_length=200),
    state: str = Query(default="", max_length=2),
    sort: HistorySort = "last_enqueued",
    direction: SortDirection = "desc",
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Read the Scale campaign ledger through its existing filter contract."""

    _require_scraper_grant(request, response, principal, settings)
    normalized_state = state.strip().upper()
    if normalized_state and normalized_state not in US_STATES:
        raise HTTPException(status_code=422, detail="Unknown state.")
    proxy_state = _state(request, settings)
    try:
        history = await _scraper_operations(
            request,
            settings,
        ).campaign_history(
            search=search.strip(),
            state=normalized_state.lower(),
            sort=sort,
            direction=direction,
        )
    except ScraperOperationsError:
        return _campaign_history_unavailable(
            request,
            settings,
            search=search.strip(),
            state=normalized_state,
            sort=sort,
            direction=direction,
        )
    _mark_operations_success(request, settings)

    def public_time(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.strftime("%b ") + str(value.day) + value.strftime(", %H:%M")

    return CampaignHistory(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        search=search.strip(),
        state=normalized_state,
        sort=sort,
        direction=direction,
        all_states=sorted(US_STATES),
        rows=[
            CampaignHistoryRow(
                keyword=row.keyword,
                state=row.state,
                cells_posted=row.cells_posted,
                first_enqueued=public_time(row.first_enqueued),
                latest_enqueued=public_time(row.latest_enqueued),
                campaign_date=(
                    row.campaign_date.strftime("%b ")
                    + str(row.campaign_date.day)
                    + row.campaign_date.strftime(", %Y")
                ),
            )
            for row in history.rows
        ],
    )


def _runtime_workspace_unavailable(
    request: Request,
    settings: Settings,
) -> RuntimeWorkspace:
    proxy_state = _state(request, settings)
    current = RuntimeConfiguration()
    return RuntimeWorkspace(
        service_state="unavailable",
        last_successful_at=_safe_last_success(proxy_state),
        current=current,
        version=runtime_version(current),
        all_states=[],
        cells=[],
        total_cells=0,
    )


@native_router.get("/runtime", response_model=RuntimeWorkspace)
async def scraper_runtime_configuration(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Project Scale's mutable runtime controls without touching Jawnix config."""

    _require_scraper_grant(request, response, principal, settings)
    parsed = await _runtime_current(request, settings)
    proxy_state = _state(request, settings)
    if parsed is None:
        return _runtime_workspace_unavailable(request, settings)
    return RuntimeWorkspace(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        current=parsed.current,
        version=parsed.version,
        all_states=parsed.all_states,
        cells=parsed.cells,
        total_cells=parsed.total_cells,
    )


@native_router.post("/runtime/preview", response_model=RuntimePreview)
async def preview_scraper_runtime_configuration(
    payload: RuntimePreviewRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Calculate live grid effects and issue a receipt for this exact review."""

    _require_scraper_grant(request, response, principal, settings)
    parsed = await _runtime_current(request, settings)
    if parsed is None:
        return _native_unavailable(_state(request, settings))
    try:
        preview = await _scraper_operations(
            request,
            settings,
        ).preview_runtime(payload)
    except ScraperOperationsError:
        return _native_unavailable(_state(request, settings))
    _mark_operations_success(request, settings)
    expected_version = preview.expected_version
    proposed_version = preview.proposed_version
    review_token = _runtime_review_serializer(settings).dumps(
        {
            "sub": str(principal.user_id),
            "expected_version": expected_version,
            "proposed_version": proposed_version,
        }
    )
    return RuntimePreview(
        configuration=payload.configuration,
        expected_version=expected_version,
        proposed_version=proposed_version,
        review_token=review_token,
        effects=preview.effects,
    )


@native_router.post("/runtime/save", response_model=RuntimeSaveResult)
async def save_scraper_runtime_configuration(
    payload: RuntimeSaveRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Save reviewed Scale runtime controls and journal them separately."""

    _require_scraper_grant(request, response, principal, settings)
    proposed_version = runtime_version(payload.configuration)
    try:
        review = _runtime_review_serializer(settings).loads(
            payload.review_token,
            max_age=REVIEW_MAX_AGE_SECONDS,
        )
        reviewed_by = uuid.UUID(str(review["sub"]))
        reviewed_current = str(review["expected_version"])
        reviewed_proposal = str(review["proposed_version"])
    except (
        BadSignature,
        SignatureExpired,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(
            status_code=422,
            detail="Preview these runtime changes again before saving.",
        ) from None
    if (
        reviewed_by != principal.user_id
        or reviewed_current != payload.expected_version
        or reviewed_proposal != proposed_version
    ):
        raise HTTPException(
            status_code=422,
            detail="Preview these runtime changes again before saving.",
        )

    async with RUNTIME_CONFIGURATION_WRITE_LOCK:
        parsed = await _runtime_current(request, settings)
        if parsed is None:
            _audit_runtime_failure(
                db,
                principal=principal,
                action="scraper_runtime_configuration_save_failed",
                reason="Scale runtime configuration could not be read before save",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedVersion": proposed_version,
                    "enqueueRequested": payload.enqueue,
                },
            )
            return _native_unavailable(_state(request, settings))
        current = parsed.current
        current_version = parsed.version
        if current_version != payload.expected_version:
            _audit_runtime_failure(
                db,
                principal=principal,
                action="scraper_runtime_configuration_save_refused",
                reason="Scale runtime configuration changed after preview",
                details={
                    "expectedVersion": payload.expected_version,
                    "currentVersion": current_version,
                    "proposedVersion": proposed_version,
                },
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Runtime configuration changed after this preview. "
                    "Reload the current settings and preview again."
                ),
            )

        try:
            saved = await _scraper_operations(
                request,
                settings,
            ).save_runtime(
                ControlRuntimeSaveRequest(
                    configuration=payload.configuration,
                    expected_version=payload.expected_version,
                    enqueue=payload.enqueue,
                )
            )
        except ScraperOperationsError as error:
            if error.status_code == 409:
                _audit_runtime_failure(
                    db,
                    principal=principal,
                    action="scraper_runtime_configuration_save_refused",
                    reason="Scale runtime configuration changed after preview",
                    details={
                        "expectedVersion": payload.expected_version,
                        "currentVersion": None,
                        "proposedVersion": proposed_version,
                    },
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Runtime configuration changed after this preview. "
                        "Reload the current settings and preview again."
                    ),
                ) from None
            _audit_runtime_failure(
                db,
                principal=principal,
                action="scraper_runtime_configuration_save_failed",
                reason="Scale runtime configuration save was refused upstream",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedVersion": proposed_version,
                    "enqueueRequested": payload.enqueue,
                    "upstreamStatus": error.status_code,
                },
            )
            return _native_unavailable(_state(request, settings))

        _mark_operations_success(request, settings)
        effects = saved.effects
        revision = ScraperRuntimeConfigurationRevision(
            before_checksum=current_version,
            after_checksum=saved.version,
            configuration=runtime_summary(payload.configuration),
            effects=effects.model_dump(mode="json"),
            enqueue_requested=payload.enqueue,
            actor_user_id=principal.user_id,
        )
        db.add(revision)
        db.flush()
        record_activity(
            db,
            action="scraper_runtime_configuration_saved",
            target_type="scraper_runtime_configuration",
            target_id=revision.id,
            actor_id=principal.user_id,
            reason=payload.reason,
            details={
                "before": {
                    "version": current_version,
                    "activeStates": current.states,
                    "totalCells": effects.current_total_cells,
                    "runtime": current.settings.model_dump(mode="json"),
                    "queue": current.queue.model_dump(mode="json"),
                    "overrideStates": sorted(current.overrides),
                },
                "after": {
                    "version": proposed_version,
                    "activeStates": payload.configuration.states,
                    "totalCells": effects.proposed_total_cells,
                    "runtime": payload.configuration.settings.model_dump(
                        mode="json"
                    ),
                    "queue": payload.configuration.queue.model_dump(
                        mode="json"
                    ),
                    "overrideStates": sorted(
                        payload.configuration.overrides
                    ),
                },
                "statesAdded": effects.states_added,
                "statesRemoved": effects.states_removed,
                "runtimeChanges": effects.runtime_changes,
                "queueChanges": effects.queue_changes,
                "overrideChanges": effects.override_changes,
                "enqueueRequested": payload.enqueue,
                "jawnixConfigurationChanged": False,
            },
        )
        db.commit()
        return RuntimeSaveResult(
            revision_id=str(revision.id),
            version=saved.version,
            configuration=payload.configuration,
            effects=effects,
            enqueued=payload.enqueue,
        )
