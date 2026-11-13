from __future__ import annotations

import hmac
import re
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from ..activity import record_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..scraper_database import (
    GENERATED_EXPORT_NAME,
    STORED_EXPORT_NAME,
    DatabaseExport,
    DatabaseStateDetail,
    DatabaseWorkspace,
    ExportRegeneration,
    MultiStateExportRequest,
    StateExportRequest,
)
from ..scraper_operations import ScraperOperationsError
from ..states import US_STATES
from .core import (
    _safe_last_success,
    _scraper_operations,
    _state,
    _workspace_now,
    native_router,
)
from .grants import _require_scraper_grant


def _database_state(state: str) -> str:
    normalized = state.strip().upper()
    if normalized not in US_STATES:
        raise HTTPException(status_code=404, detail="Unknown state.")
    return normalized


def _database_unavailable(
    request: Request,
    settings: Settings,
) -> DatabaseWorkspace:
    state = _state(request, settings)
    return DatabaseWorkspace(
        service_state="unavailable",
        last_successful_at=_safe_last_success(state),
    )


@native_router.get(
    "/database",
    response_model=DatabaseWorkspace,
)
async def scraper_database_workspace(
    request: Request,
    response: Response,
    search: str = Query(default="", max_length=500),
    state: str = Query(default="", max_length=2),
    page: int = Query(default=1, ge=1),
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Business browsing, totals, state summaries, and stored exports."""

    _require_scraper_grant(request, response, principal, settings)
    normalized_state = ""
    if state.strip():
        normalized_state = _database_state(state)
    try:
        workspace = await _scraper_operations(
            request,
            settings,
        ).database_workspace(
            search=search.strip(),
            state=normalized_state.lower(),
            page=page,
        )
    except ScraperOperationsError:
        return _database_unavailable(request, settings)
    proxy_state = _state(request, settings)
    proxy_state.last_success = _workspace_now(request)
    return DatabaseWorkspace(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        totals=workspace.totals,
        states=workspace.states,
        browse=workspace.browse,
        stored_exports=workspace.stored_exports,
    )


@native_router.get(
    "/database/states/{state}",
    response_model=DatabaseStateDetail,
)
async def scraper_database_state_detail(
    state: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Totals and Niche context for one current Scraper database state."""

    _require_scraper_grant(request, response, principal, settings)
    normalized_state = _database_state(state)
    proxy_state = _state(request, settings)
    try:
        detail = await _scraper_operations(
            request,
            settings,
        ).database_state(normalized_state.lower())
    except ScraperOperationsError:
        return DatabaseStateDetail(
            service_state="unavailable",
            last_successful_at=_safe_last_success(proxy_state),
            state=normalized_state,
        )
    proxy_state.last_success = _workspace_now(request)
    return DatabaseStateDetail(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        state=normalized_state,
        totals=detail.totals,
        niches=detail.niches,
    )


def _safe_download_error(status_code: int) -> JSONResponse:
    if status_code == 404:
        return JSONResponse(
            status_code=404,
            content={"detail": "The requested Scraper export was not found."},
        )
    if status_code in {400, 422}:
        return JSONResponse(
            status_code=status_code,
            content={"detail": "The requested Scraper export is invalid."},
        )
    return JSONResponse(
        status_code=503,
        content={"detail": "Scraper exports are unavailable."},
    )


def _database_export_response(
    request: Request,
    settings: Settings,
    *,
    export: DatabaseExport,
    filename_pattern: re.Pattern[str],
    exact_filename: str | None = None,
) -> Response:
    """Return a validated typed export with the existing public headers."""

    if not filename_pattern.fullmatch(export.filename):
        return _safe_download_error(503)
    if exact_filename is not None and not hmac.compare_digest(
        export.filename,
        exact_filename,
    ):
        return _safe_download_error(503)
    _state(request, settings).last_success = _workspace_now(request)
    return Response(
        content=export.content,
        media_type=export.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{export.filename}"'
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@native_router.get("/database/exports/state/{state}")
async def download_scraper_state_export(
    state: str,
    request: Request,
    response: Response,
    scope: Literal["all", "selected"] = "all",
    niche: list[str] | None = Query(default=None),
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Stream one state's current phone export with legacy filename semantics."""

    _require_scraper_grant(request, response, principal, settings)
    normalized_state = _database_state(state)
    if scope == "selected" and not niche:
        raise HTTPException(
            status_code=422,
            detail="Select at least one Niche.",
        )
    if scope == "all" and niche:
        raise HTTPException(
            status_code=400,
            detail="Niche selection requires selected scope.",
        )
    try:
        export = await _scraper_operations(
            request,
            settings,
        ).export_database_state(
            normalized_state.lower(),
            StateExportRequest(
                niches=None if scope == "all" else niche,
            ),
        )
    except ScraperOperationsError as error:
        result = _safe_download_error(error.status_code or 503)
    else:
        result = _database_export_response(
            request,
            settings,
            export=export,
            filename_pattern=GENERATED_EXPORT_NAME,
        )
    return result


@native_router.get("/database/exports/states")
async def download_scraper_multi_state_export(
    request: Request,
    response: Response,
    state: list[str] | None = Query(default=None),
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Stream multiple states with one header and the legacy bulk filename."""

    _require_scraper_grant(request, response, principal, settings)
    normalized_states = list(
        dict.fromkeys(_database_state(value) for value in state or [])
    )
    if not normalized_states:
        raise HTTPException(
            status_code=422,
            detail="Select at least one state.",
        )
    try:
        export = await _scraper_operations(
            request,
            settings,
        ).export_database_states(
            MultiStateExportRequest(
                states=[value.lower() for value in normalized_states],
            )
        )
    except ScraperOperationsError as error:
        result = _safe_download_error(error.status_code or 503)
    else:
        result = _database_export_response(
            request,
            settings,
            export=export,
            filename_pattern=GENERATED_EXPORT_NAME,
        )
    return result


@native_router.get("/database/exports/stored/{filename}")
async def download_stored_scraper_export(
    filename: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Download one validated stored export without accepting a path."""

    _require_scraper_grant(request, response, principal, settings)
    if not STORED_EXPORT_NAME.fullmatch(filename):
        raise HTTPException(
            status_code=400,
            detail="Invalid export filename.",
        )
    try:
        export = await _scraper_operations(
            request,
            settings,
        ).stored_database_export(filename)
    except ScraperOperationsError as error:
        result = _safe_download_error(error.status_code or 503)
    else:
        result = _database_export_response(
            request,
            settings,
            export=export,
            filename_pattern=STORED_EXPORT_NAME,
            exact_filename=filename,
        )
    return result


@native_router.post(
    "/database/exports/{state}/regenerate",
    response_model=ExportRegeneration,
)
async def regenerate_scraper_exports(
    state: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Regenerate the current stored state exports through their existing owner."""

    _require_scraper_grant(request, response, principal, settings)
    normalized_state = _database_state(state)
    try:
        result = await _scraper_operations(
            request,
            settings,
        ).regenerate_database_exports(normalized_state.lower())
    except ScraperOperationsError:
        raise HTTPException(
            status_code=503,
            detail="Scraper exports are unavailable.",
        ) from None
    _state(request, settings).last_success = _workspace_now(request)
    record_activity(
        db,
        action="scraper_exports_regenerated",
        target_type="scraper_export",
        target_id=normalized_state,
        actor_id=principal.user_id,
        reason="Regenerated stored Scraper exports from the current pool.",
        details={
            "requestedState": normalized_state,
            "generated": result.generated,
            "storedExportCount": len(result.stored_exports),
        },
        known_secrets=(
            settings.scraper_ops_url,
            settings.scraper_ops_user,
            settings.scraper_ops_password,
        ),
    )
    db.commit()
    return result
