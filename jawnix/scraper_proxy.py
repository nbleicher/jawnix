from __future__ import annotations

import asyncio
import html
import hmac
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from .activity import record_activity
from .admin_mfa_api import (
    _factors,
    _identity_for_token,
    _record_factor_use,
    _record_failure,
    _reset_failures,
    _session_json,
    _state as _admin_mfa_state,
    _throttle_status,
    _verified,
    _verify_factor,
)
from .auth import Principal, require_admin
from .config import Settings, get_settings
from .database import get_db
from .mfa_provider import MFAProviderError, get_mfa_provider
from .models import ScraperRuntimeConfigurationRevision
from .schemas import AdminMFAChallenge
from .scraper_monitoring import (
    REGION_INTERVALS,
    REGIONS,
    MonitoringRegion,
    MonitoringSnapshot,
    RegionKey,
    region_data,
    snapshot_regions,
)
from .scraper_keywords import (
    KeywordDiff,
    KeywordGenerateRequest,
    KeywordGenerationDraft,
    KeywordRollover,
    KeywordRolloverRequest,
    KeywordSaveRequest,
    KeywordSaveResult,
    KeywordTextRequest,
    KeywordWorkspace,
    diff_keywords,
    keyword_version,
)
from .scraper_operations import (
    HTTPScraperOperations,
    ScraperOperations,
    ScraperOperationsError,
)
from .scraper_coverage import (
    STATE_CARDS_REFRESH_SECONDS,
    STATE_CELLS_REFRESH_SECONDS,
    STATE_KEYWORDS_REFRESH_SECONDS,
    CoverageContractError,
    CoverageFeed,
    StateCoverageDetail,
    StateCoverageSnapshot,
    StateGridCoverage,
    StateKeywordActivity,
    parse_state_cards,
    parse_state_cells,
    parse_state_keywords,
)
from .scraper_database import (
    GENERATED_EXPORT_NAME,
    STORED_EXPORT_NAME,
    DatabaseContractError,
    DatabaseStateDetail,
    DatabaseWorkspace,
    ExportRegeneration,
    StoredExport,
    parse_database_state,
    parse_database_workspace,
    parse_export_regeneration,
)
from .scraper_runtime import (
    CampaignHistory,
    HistorySort,
    RuntimeConfiguration,
    RuntimePreview,
    RuntimePreviewRequest,
    RuntimeSaveRequest,
    RuntimeSaveResult,
    RuntimeWorkspace,
    SortDirection,
    calculate_effects,
    parse_campaign_history,
    parse_cell_effects,
    parse_runtime_workspace,
    runtime_form,
    runtime_summary,
    runtime_version,
)
from .states import US_STATES


logger = logging.getLogger(__name__)

MOUNT_PREFIX = "/admin/scraper"
SCRAPER_SESSION_COOKIE = "jawnix_scraper_session"
HANDOFF_MAX_AGE_SECONDS = 60
SCRAPER_GRANT_COOKIE = "jawnix_scraper_grant"
SCRAPER_IDLE_SECONDS = 15 * 60
KEYWORD_WRITE_LOCK = asyncio.Lock()
RUNTIME_CONFIGURATION_WRITE_LOCK = asyncio.Lock()
FORWARDED_REQUEST_HEADERS = {
    "accept",
    "accept-encoding",
    "content-type",
    "hx-current-url",
    "hx-request",
    "hx-target",
    "hx-trigger",
    "user-agent",
}
FORWARDED_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-type",
    "etag",
    "last-modified",
}
PATH_ATTRIBUTE = re.compile(
    r'(?P<attribute>(?:href|src|action|hx-(?:get|post|put|patch|delete)))'
    r'=(?P<quote>["\'])/(?P<path>(?!/)[^"\']*)'
)


@dataclass
class ScraperProxyState:
    settings_key: tuple[str, str, float]
    last_success: datetime | None = None


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


native_router = APIRouter(
    prefix="/api/admin/scraper",
    tags=["scraper-operations"],
)


def _scraper_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="jawnix-scraper-session-v1",
    )


def _handoff_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="jawnix-scraper-handoff-v1",
    )


def _grant_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="jawnix-scraper-privileged-v1",
    )


def _keyword_review_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="jawnix-scraper-keyword-review-v1",
    )


def _runtime_review_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="jawnix-scraper-runtime-review-v1",
    )


def _workspace_now(request: Request) -> datetime:
    clock = getattr(request.app.state, "scraper_workspace_clock", None)
    value = clock() if clock else datetime.now(timezone.utc)
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _clear_grant(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SCRAPER_GRANT_COOKIE,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/api/admin/scraper",
    )


def _issue_grant(
    response: Response,
    request: Request,
    principal: Principal,
    settings: Settings,
) -> None:
    token = _grant_serializer(settings).dumps(
        {
            "sub": str(principal.user_id),
            "generation": principal.session_generation,
            "last_activity": _workspace_now(request).timestamp(),
        }
    )
    response.set_cookie(
        SCRAPER_GRANT_COOKIE,
        token,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/api/admin/scraper",
        max_age=SCRAPER_IDLE_SECONDS,
    )


def _require_scraper_grant(
    request: Request,
    response: Response,
    principal: Principal,
    settings: Settings,
) -> None:
    token = request.cookies.get(SCRAPER_GRANT_COOKIE, "")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="A fresh Scraper authenticator challenge is required.",
        )
    try:
        payload = _grant_serializer(settings).loads(token)
        issued_to = uuid.UUID(str(payload["sub"]))
        generation = int(payload["generation"])
        last_activity = datetime.fromtimestamp(
            float(payload["last_activity"]),
            tz=timezone.utc,
        )
    except (BadSignature, KeyError, TypeError, ValueError, OverflowError):
        _clear_grant(response, settings)
        raise HTTPException(
            status_code=401,
            detail="A fresh Scraper authenticator challenge is required.",
        ) from None
    if (
        issued_to != principal.user_id
        or generation != principal.session_generation
    ):
        _clear_grant(response, settings)
        raise HTTPException(
            status_code=401,
            detail="A fresh Scraper authenticator challenge is required.",
        )
    if (
        _workspace_now(request) - last_activity
        > timedelta(seconds=SCRAPER_IDLE_SECONDS)
    ):
        _clear_grant(response, settings)
        raise HTTPException(
            status_code=401,
            detail="Scraper privileged session expired.",
        )
    _issue_grant(response, request, principal, settings)


def _safe_last_success(state: ScraperProxyState) -> str | None:
    if state.last_success is None:
        return None
    return state.last_success.astimezone(timezone.utc).isoformat()


def _native_unavailable(state: ScraperProxyState) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Scraper Operations is unavailable.",
            "lastSuccessfulAt": _safe_last_success(state),
        },
    )


async def _raw_native_upstream(
    request: Request,
    settings: Settings,
    *,
    method: str,
    path: str,
    form: dict[str, object] | None = None,
    timeout: float | None = None,
) -> httpx.Response | None:
    if (
        not settings.scraper_ops_url
        or not settings.scraper_ops_user
        or not settings.scraper_ops_password
    ):
        return None
    transport = getattr(request.app.state, "scraper_proxy_transport", None)
    started_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            base_url=settings.scraper_ops_url.rstrip("/"),
            auth=httpx.BasicAuth(
                settings.scraper_ops_user,
                settings.scraper_ops_password,
            ),
            timeout=(
                settings.scraper_ops_timeout_seconds
                if timeout is None
                else timeout
            ),
            follow_redirects=False,
            transport=transport,
        ) as client:
            upstream = await client.request(method, path, data=form)
    except httpx.RequestError as error:
        transport_error = type(error).__name__
        logger.warning(
            "Scraper upstream request failed: transport_error=%s "
            "method=%s path=%s elapsed_seconds=%.3f",
            transport_error,
            method,
            path,
            time.perf_counter() - started_at,
        )
        return None
    return upstream


async def _native_upstream(
    request: Request,
    settings: Settings,
    *,
    method: str,
    path: str,
    form: dict[str, object] | None = None,
    timeout: float | None = None,
) -> httpx.Response | None:
    upstream = await _raw_native_upstream(
        request,
        settings,
        method=method,
        path=path,
        form=form,
        timeout=timeout,
    )
    if upstream is None:
        return None
    if not 200 <= upstream.status_code < 300:
        return None
    _state(request, settings).last_success = _workspace_now(request)
    return upstream


@native_router.post("/entry")
async def begin_scraper_entry(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Invalidate any prior grant before presenting a new factor challenge."""

    _clear_grant(response, settings)
    provider = get_mfa_provider(settings)
    factors = _verified(await _factors(provider, principal))
    return {
        "factors": [
            {
                "id": str(factor.id),
                "name": factor.friendly_name,
                "type": factor.factor_type,
            }
            for factor in factors
        ],
        "idleExpiresIn": SCRAPER_IDLE_SECONDS,
    }


@native_router.post("/step-up")
async def complete_scraper_step_up(
    payload: AdminMFAChallenge,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Require a real provider challenge; an existing AAL2 claim is insufficient."""

    state = _admin_mfa_state(db, principal.user_id)
    throttled, _ = _throttle_status(state, settings)
    if throttled:
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again after the stated time.",
            headers={"Retry-After": str(settings.admin_mfa_lock_seconds)},
        )
    provider = get_mfa_provider(settings)
    await _identity_for_token(provider, principal, payload.access_token)
    factors = await _factors(provider, principal)
    if payload.factor_id not in {
        factor.id for factor in _verified(factors)
    }:
        raise HTTPException(
            status_code=422,
            detail="That authenticator is unavailable.",
        )
    try:
        provider_session = await _verify_factor(
            provider=provider,
            access_token=payload.access_token,
            factor_id=payload.factor_id,
            code=payload.code,
        )
    except MFAProviderError as exc:
        if not exc.invalid_code:
            record_activity(
                db,
                action="scraper_step_up_unavailable",
                target_type="scraper_privileged_session",
                target_id=principal.user_id,
                actor_id=principal.user_id,
                reason="Fresh Scraper authenticator challenge was unavailable",
            )
            db.commit()
            raise HTTPException(
                status_code=503,
                detail="Scraper verification is temporarily unavailable.",
            ) from None
        locked_until = _record_failure(
            db,
            state,
            settings,
            principal,
            request,
        )
        if locked_until:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Try again after the stated time.",
                headers={"Retry-After": str(settings.admin_mfa_lock_seconds)},
            ) from None
        raise HTTPException(
            status_code=422,
            detail="That code could not be verified. Check it and try again.",
        ) from None
    _, claims = await _identity_for_token(
        provider,
        principal,
        provider_session.access_token,
    )
    if str(claims.get("aal") or "") != "aal2":
        raise HTTPException(
            status_code=502,
            detail="The verification service returned an incomplete session.",
        )
    _reset_failures(state)
    _record_factor_use(db, principal, payload.factor_id, request)
    record_activity(
        db,
        action="scraper_step_up_succeeded",
        target_type="scraper_privileged_session",
        target_id=principal.user_id,
        actor_id=principal.user_id,
        reason="Fresh authenticator challenge completed for Scraper Operations",
        details={"idleExpiresIn": SCRAPER_IDLE_SECONDS},
        known_secrets=(payload.access_token, payload.code),
    )
    db.commit()
    _issue_grant(response, request, principal, settings)
    return {
        "ok": True,
        "idleExpiresIn": SCRAPER_IDLE_SECONDS,
        "session": _session_json(provider_session),
    }


@native_router.get("/workspace")
async def scraper_workspace(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    _require_scraper_grant(
        request,
        response,
        principal,
        settings,
    )
    upstream = await _native_upstream(
        request,
        settings,
        method="GET",
        path="/dashboard",
    )
    state = _state(request, settings)
    if upstream is None:
        return _native_unavailable(state)
    return {
        "serviceState": "connected",
        "lastSuccessfulAt": _safe_last_success(state),
        "idleExpiresIn": SCRAPER_IDLE_SECONDS,
    }


async def _native_json(
    request: Request,
    settings: Settings,
    *,
    path: str,
) -> dict | None:
    """A JSON read from the Scraper projection, or None if it did not answer."""

    upstream = await _native_upstream(
        request,
        settings,
        method="GET",
        path=path,
    )
    if upstream is None:
        return None
    try:
        payload = upstream.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


async def _coverage_fragment(
    request: Request,
    settings: Settings,
    *,
    path: str,
    parser,
):
    """Parse one reviewed GMS/OPS fragment without exposing its markup."""

    upstream = await _raw_native_upstream(
        request,
        settings,
        method="GET",
        path=path,
    )
    if upstream is None or not 200 <= upstream.status_code < 300:
        return None
    try:
        return parser(upstream.text)
    except (CoverageContractError, UnicodeError):
        return None


async def _database_markup(
    request: Request,
    settings: Settings,
    *,
    path: str,
) -> str | None:
    """One reviewed database page without exposing its private location."""

    upstream = await _raw_native_upstream(
        request,
        settings,
        method="GET",
        path=path,
    )
    if upstream is None or not 200 <= upstream.status_code < 300:
        return None
    content_type = upstream.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        return None
    try:
        return upstream.text
    except UnicodeError:
        return None


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
        idle_expires_in=SCRAPER_IDLE_SECONDS,
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
    query = urlencode(
        {
            "search": search.strip(),
            "state": normalized_state.lower(),
            "page": page,
        }
    )
    markup = await _database_markup(
        request,
        settings,
        path=f"/database?{query}",
    )
    if markup is None:
        return _database_unavailable(request, settings)
    try:
        totals, states, browse, embedded_exports = parse_database_workspace(
            markup,
            search=search,
            state=normalized_state,
            requested_page=page,
        )
    except DatabaseContractError:
        return _database_unavailable(request, settings)
    probed_exports = await _probe_stored_exports(
        request,
        settings,
        [item.state for item in states],
    )
    stored_exports = probed_exports or embedded_exports
    proxy_state = _state(request, settings)
    proxy_state.last_success = _workspace_now(request)
    return DatabaseWorkspace(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        idle_expires_in=SCRAPER_IDLE_SECONDS,
        totals=totals,
        states=states,
        browse=browse,
        stored_exports=stored_exports,
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
    markup = await _database_markup(
        request,
        settings,
        path=f"/database/states/{normalized_state.lower()}",
    )
    proxy_state = _state(request, settings)
    if markup is None:
        return DatabaseStateDetail(
            service_state="unavailable",
            last_successful_at=_safe_last_success(proxy_state),
            idle_expires_in=SCRAPER_IDLE_SECONDS,
            state=normalized_state,
        )
    try:
        totals, niches = parse_database_state(
            markup,
            expected_state=normalized_state,
        )
    except DatabaseContractError:
        return DatabaseStateDetail(
            service_state="unavailable",
            last_successful_at=_safe_last_success(proxy_state),
            idle_expires_in=SCRAPER_IDLE_SECONDS,
            state=normalized_state,
        )
    proxy_state.last_success = _workspace_now(request)
    return DatabaseStateDetail(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        idle_expires_in=SCRAPER_IDLE_SECONDS,
        state=normalized_state,
        totals=totals,
        niches=niches,
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


def _download_filename(
    upstream: httpx.Response,
    *,
    expected: re.Pattern[str],
    exact: str | None = None,
) -> str | None:
    disposition = upstream.headers.get("content-disposition", "")
    match = re.search(
        r'(?:^|;)\s*filename="(?P<filename>[^"\r\n;]+)"(?:;|$)',
        disposition,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    filename = match.group("filename")
    if exact is not None and not hmac.compare_digest(filename, exact):
        return None
    return filename if expected.fullmatch(filename) else None


async def _probe_stored_exports(
    request: Request,
    settings: Settings,
    states: list[str],
) -> list[StoredExport]:
    """Read only stored-file headers; never buffer every state CSV to list it."""

    if (
        not settings.scraper_ops_url
        or not settings.scraper_ops_user
        or not settings.scraper_ops_password
        or not states
    ):
        return []
    transport = getattr(request.app.state, "scraper_proxy_transport", None)
    async with httpx.AsyncClient(
        base_url=settings.scraper_ops_url.rstrip("/"),
        auth=httpx.BasicAuth(
            settings.scraper_ops_user,
            settings.scraper_ops_password,
        ),
        timeout=settings.scraper_ops_timeout_seconds,
        follow_redirects=False,
        transport=transport,
    ) as client:
        async def probe(state: str) -> StoredExport | None:
            filename = f"{state}.csv"
            try:
                upstream = await client.send(
                    client.build_request(
                        "GET",
                        f"/database/download/{filename}",
                        headers={"Accept": "text/csv"},
                    ),
                    stream=True,
                )
            except httpx.RequestError:
                return None
            try:
                if upstream.status_code != 200:
                    return None
                if "text/csv" not in upstream.headers.get(
                    "content-type",
                    "",
                ).lower():
                    return None
                safe_name = _download_filename(
                    upstream,
                    expected=STORED_EXPORT_NAME,
                    exact=filename,
                )
                raw_size = upstream.headers.get("content-length", "")
                if safe_name is None or not raw_size.isdigit():
                    return None
                return StoredExport(
                    filename=safe_name,
                    size_label=f"{int(raw_size) / 1024:.1f} KB",
                )
            finally:
                await upstream.aclose()

        found = await asyncio.gather(*(probe(state) for state in states))
    return [item for item in found if item is not None]


async def _stream_database_export(
    request: Request,
    settings: Settings,
    *,
    path: str,
    filename_pattern: re.Pattern[str],
    exact_filename: str | None = None,
) -> Response:
    """Relay CSV bytes while keeping every upstream detail server-side."""

    if (
        not settings.scraper_ops_url
        or not settings.scraper_ops_user
        or not settings.scraper_ops_password
    ):
        return _safe_download_error(503)
    transport = getattr(request.app.state, "scraper_proxy_transport", None)
    client = httpx.AsyncClient(
        base_url=settings.scraper_ops_url.rstrip("/"),
        auth=httpx.BasicAuth(
            settings.scraper_ops_user,
            settings.scraper_ops_password,
        ),
        timeout=settings.scraper_ops_timeout_seconds,
        follow_redirects=False,
        transport=transport,
    )
    try:
        upstream = await client.send(
            client.build_request(
                "GET",
                path,
                headers={"Accept": "text/csv"},
            ),
            stream=True,
        )
    except httpx.RequestError:
        await client.aclose()
        return _safe_download_error(503)
    if not 200 <= upstream.status_code < 300:
        status_code = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        return _safe_download_error(status_code)
    content_type = upstream.headers.get("content-type", "").lower()
    filename = _download_filename(
        upstream,
        expected=filename_pattern,
        exact=exact_filename,
    )
    if "text/csv" not in content_type or filename is None:
        await upstream.aclose()
        await client.aclose()
        return _safe_download_error(503)
    _state(request, settings).last_success = _workspace_now(request)

    async def stream_body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        except httpx.RequestError:
            # Once streaming starts, changing the response status would itself
            # corrupt the CSV. Closing the response produces a failed download
            # without appending topology, credentials, or provider text.
            return
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_body(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
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
    query_items: list[tuple[str, str]] = [("scope", scope)]
    query_items.extend(("keyword", value) for value in niche or [])
    result = await _stream_database_export(
        request,
        settings,
        path=(
            f"/database/states/{normalized_state.lower()}/download?"
            f"{urlencode(query_items)}"
        ),
        filename_pattern=GENERATED_EXPORT_NAME,
    )
    _issue_grant(result, request, principal, settings)
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
    result = await _stream_database_export(
        request,
        settings,
        path=(
            "/database/bulk-download?"
            + urlencode(
                [("state", value.lower()) for value in normalized_states]
            )
        ),
        filename_pattern=GENERATED_EXPORT_NAME,
    )
    _issue_grant(result, request, principal, settings)
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
    result = await _stream_database_export(
        request,
        settings,
        path=f"/database/download/{filename}",
        filename_pattern=STORED_EXPORT_NAME,
        exact_filename=filename,
    )
    _issue_grant(result, request, principal, settings)
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
    upstream = await _native_upstream(
        request,
        settings,
        method="POST",
        path=f"/database/export/{normalized_state.lower()}",
    )
    if upstream is None:
        raise HTTPException(
            status_code=503,
            detail="Scraper exports are unavailable.",
        )
    try:
        result = parse_export_regeneration(upstream.text)
    except (DatabaseContractError, UnicodeError):
        raise HTTPException(
            status_code=503,
            detail="Scraper exports are unavailable.",
        ) from None
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


def _coverage_state(state: str) -> str:
    normalized = state.strip().upper()
    if normalized not in US_STATES:
        raise HTTPException(status_code=404, detail="Unknown state.")
    return normalized


def _coverage_feed(data, *, refresh_seconds: int, request: Request):
    return CoverageFeed(
        state="ok" if data is not None else "unavailable",
        refresh_seconds=refresh_seconds,
        fetched_at=_workspace_now(request) if data is not None else None,
        data=data,
    )


@native_router.get(
    "/coverage",
    response_model=StateCoverageSnapshot,
)
async def scraper_state_coverage(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Active-state counts and coverage at the legacy twenty-second cadence."""

    _require_scraper_grant(request, response, principal, settings)
    cards = await _coverage_fragment(
        request,
        settings,
        path="/frag/states/cards",
        parser=parse_state_cards,
    )
    state = _state(request, settings)
    return StateCoverageSnapshot(
        service_state=(
            "connected" if cards is not None else "unavailable"
        ),
        last_successful_at=state.last_success,
        idle_expires_in=SCRAPER_IDLE_SECONDS,
        states=_coverage_feed(
            cards,
            refresh_seconds=STATE_CARDS_REFRESH_SECONDS,
            request=request,
        ),
    )


@native_router.get(
    "/coverage/{state}",
    response_model=StateCoverageDetail,
)
async def scraper_state_coverage_detail(
    state: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """One state's independently refreshable keyword and grid regions."""

    _require_scraper_grant(request, response, principal, settings)
    state = _coverage_state(state)
    state_path = state.lower()
    keywords = await _coverage_fragment(
        request,
        settings,
        path=f"/frag/states/{state_path}/keywords",
        parser=parse_state_keywords,
    )
    cells = await _coverage_fragment(
        request,
        settings,
        path=f"/frag/states/{state_path}/cells",
        parser=parse_state_cells,
    )
    available = int(keywords is not None) + int(cells is not None)
    service_state = (
        "connected"
        if available == 2
        else "degraded"
        if available == 1
        else "unavailable"
    )
    proxy_state = _state(request, settings)
    return StateCoverageDetail(
        state=state,
        service_state=service_state,
        last_successful_at=proxy_state.last_success,
        idle_expires_in=SCRAPER_IDLE_SECONDS,
        keywords=_coverage_feed(
            keywords,
            refresh_seconds=STATE_KEYWORDS_REFRESH_SECONDS,
            request=request,
        ),
        cells=_coverage_feed(
            cells,
            refresh_seconds=STATE_CELLS_REFRESH_SECONDS,
            request=request,
        ),
    )


@native_router.get(
    "/coverage/{state}/keywords",
    response_model=CoverageFeed[list[StateKeywordActivity]],
)
async def scraper_state_keyword_coverage(
    state: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Keyword activity for one state, isolated from grid-cell failures."""

    _require_scraper_grant(request, response, principal, settings)
    state = _coverage_state(state)
    keywords = await _coverage_fragment(
        request,
        settings,
        path=f"/frag/states/{state.lower()}/keywords",
        parser=parse_state_keywords,
    )
    return _coverage_feed(
        keywords,
        refresh_seconds=STATE_KEYWORDS_REFRESH_SECONDS,
        request=request,
    )


@native_router.get(
    "/coverage/{state}/cells",
    response_model=CoverageFeed[StateGridCoverage],
)
async def scraper_state_grid_coverage(
    state: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Today's Posted, Reserved, Failed, and Uncovered grid cells."""

    _require_scraper_grant(request, response, principal, settings)
    state = _coverage_state(state)
    cells = await _coverage_fragment(
        request,
        settings,
        path=f"/frag/states/{state.lower()}/cells",
        parser=parse_state_cells,
    )
    return _coverage_feed(
        cells,
        refresh_seconds=STATE_CELLS_REFRESH_SECONDS,
        request=request,
    )


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
):
    """Every monitoring region at once, for the workspace's first paint.

    An outage answers 200 with `service_state: unavailable` rather than an
    error status. The Jawnix request did succeed; what it reports is the
    Scraper's health, and a client that receives a well-formed snapshot can
    show the outage while keeping whatever it already had on screen.
    """

    _require_scraper_grant(request, response, principal, settings)
    payload = await _native_json(request, settings, path="/api/dashboard")
    state = _state(request, settings)
    if payload is None:
        return MonitoringSnapshot(
            service_state="unavailable",
            last_successful_at=state.last_success,
            idle_expires_in=SCRAPER_IDLE_SECONDS,
            regions=[_unavailable_region(region) for region in REGIONS],
        )
    return MonitoringSnapshot(
        service_state="connected",
        last_successful_at=state.last_success,
        idle_expires_in=SCRAPER_IDLE_SECONDS,
        regions=snapshot_regions(payload, fetched_at=_workspace_now(request)),
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
    payload = await _native_json(
        request,
        settings,
        path=f"/api/dashboard/{region}",
    )
    if payload is None:
        return _unavailable_region(region)
    return MonitoringRegion(
        region=region,
        state="ok",
        refresh_seconds=REGION_INTERVALS[region],
        fetched_at=_workspace_now(request),
        data=region_data(region, payload),
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
    form = {"action": payload.action}
    if payload.action == "pause":
        form["clear_queue"] = "yes" if payload.clear_queue else "no"
    upstream = await _native_upstream(
        request,
        settings,
        method="POST",
        path="/dashboard/pipeline",
        form=form,
    )
    state = _state(request, settings)
    if upstream is None:
        return _native_unavailable(state)

    # Read the resulting activity back rather than predicting it. The cancelled
    # job count only exists upstream, and an audit entry that guessed it would
    # be worse than one that recorded nothing.
    activity = await _native_json(
        request,
        settings,
        path="/api/dashboard/activity",
    )
    region = (
        MonitoringRegion(
            region="activity",
            state="ok",
            refresh_seconds=REGION_INTERVALS["activity"],
            fetched_at=_workspace_now(request),
            data=region_data("activity", activity),
        )
        if activity is not None
        else _unavailable_region("activity")
    )
    pipeline_state = (
        region.data.pipeline_state.key
        if region.data is not None and region.data.pipeline_state is not None
        else ("paused" if payload.action == "pause" else "running")
    )
    cancelled_jobs = (
        region.data.pause_info.cancelled_jobs
        if region.data is not None and region.data.pause_info is not None
        else 0
    )

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


def _mark_keyword_success(request: Request, settings: Settings) -> None:
    _state(request, settings).last_success = _workspace_now(request)


_SAFE_GENERATION_ERRORS = (
    "Unsupported generation mode",
    "The selected winner is unavailable",
    "AI generation is not configured",
    "The AI provider ",
    "The OpenRouter ",
    "OpenRouter ",
    "The DeepSeek ",
    "DeepSeek ",
    "AI generation failed",
    "AI could not produce ",
    "Another keyword generation ",
    "Choose a winner ",
)


def _safe_generation_error(message: str | None) -> str:
    if message and len(message) <= 240 and message.startswith(
        _SAFE_GENERATION_ERRORS
    ):
        return message
    return "AI keyword generation failed; try again."


def _audit_keyword_failure(
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
        target_type="scraper_keywords",
        target_id="primary",
        actor_id=principal.user_id,
        reason=reason,
        details=details,
    )
    db.commit()


def _scraper_operations(
    request: Request,
    settings: Settings,
) -> ScraperOperations:
    override = getattr(request.app.state, "scraper_operations", None)
    if override is not None:
        return override
    return HTTPScraperOperations(
        settings,
        transport=getattr(request.app.state, "scraper_proxy_transport", None),
    )


def _public_rollover(rollover: KeywordRollover) -> KeywordRollover:
    if rollover.state != "off":
        return rollover
    return rollover.model_copy(
        update={"posted_jobs": None, "expected_jobs": None}
    )


def _keyword_workspace_unavailable(
    request: Request,
    settings: Settings,
) -> KeywordWorkspace:
    state = _state(request, settings)
    current: list[str] = []
    return KeywordWorkspace(
        service_state="unavailable",
        last_successful_at=_safe_last_success(state),
        current=current,
        version=keyword_version(current),
        ai_enabled=False,
        rollover=KeywordRollover(
            enabled=False,
            state="off",
            label="Unavailable",
            detail="No current rollover status is available.",
            percent_complete=0,
        ),
        winners=[],
        idle_expires_in=SCRAPER_IDLE_SECONDS,
    )


@native_router.get("/keywords", response_model=KeywordWorkspace)
async def scraper_keyword_workspace(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Project every current keyword read without moving ownership to Jawnix."""

    _require_scraper_grant(request, response, principal, settings)
    operations = _scraper_operations(request, settings)
    try:
        workspace, winners = await asyncio.gather(
            operations.list_keywords(),
            operations.keyword_winners(),
        )
    except ScraperOperationsError:
        return _keyword_workspace_unavailable(request, settings)
    _mark_keyword_success(request, settings)
    proxy_state = _state(request, settings)
    return KeywordWorkspace(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        current=workspace.current,
        version=workspace.version,
        ai_enabled=workspace.ai_enabled,
        rollover=_public_rollover(workspace.rollover),
        winners=winners,
        idle_expires_in=SCRAPER_IDLE_SECONDS,
    )


@native_router.post("/keywords/preview", response_model=KeywordDiff)
async def preview_scraper_keywords(
    payload: KeywordTextRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Preview is a fresh read, so its version can safely gate the later save."""

    _require_scraper_grant(request, response, principal, settings)
    try:
        diff = await _scraper_operations(request, settings).preview_keywords(
            payload
        )
    except ScraperOperationsError as error:
        if error.status_code == 422 and error.detail:
            _mark_keyword_success(request, settings)
            raise HTTPException(status_code=422, detail=error.detail) from None
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keywords_preview_failed",
            reason="Scraper keyword preview could not reach the owning service",
            details={"outcome": "upstream_unavailable"},
        )
        return _native_unavailable(_state(request, settings))
    _mark_keyword_success(request, settings)
    diff.review_token = _keyword_review_serializer(settings).dumps(
        {
            "sub": str(principal.user_id),
            "expected_version": diff.expected_version,
            "proposed_version": keyword_version(diff.proposed),
        }
    )
    return diff


@native_router.post("/keywords/save", response_model=KeywordSaveResult)
async def save_scraper_keywords(
    payload: KeywordSaveRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Compare-and-save through the Scraper's existing atomic writer."""

    _require_scraper_grant(request, response, principal, settings)
    proposed = diff_keywords([], payload.text).proposed
    if not proposed:
        raise HTTPException(
            status_code=422,
            detail="At least one keyword is required.",
        )
    try:
        review = _keyword_review_serializer(settings).loads(
            payload.review_token,
            max_age=SCRAPER_IDLE_SECONDS,
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
            detail="Preview these keyword changes again before saving.",
        ) from None
    if (
        reviewed_by != principal.user_id
        or reviewed_current != payload.expected_version
        or reviewed_proposal != keyword_version(proposed)
    ):
        raise HTTPException(
            status_code=422,
            detail="Preview these keyword changes again before saving.",
        )
    generation_id: str | None = None
    if payload.generation_id:
        try:
            generation_id = str(uuid.UUID(payload.generation_id))
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail="Invalid keyword generation.",
            ) from error

    async with KEYWORD_WRITE_LOCK:
        operations = _scraper_operations(request, settings)
        try:
            workspace = await operations.list_keywords()
        except ScraperOperationsError:
            _audit_keyword_failure(
                db,
                principal=principal,
                action="scraper_keywords_save_failed",
                reason="Scraper keyword save could not reach the owning service",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedCount": len(proposed),
                    "enqueueRequested": payload.enqueue,
                },
            )
            return _native_unavailable(_state(request, settings))
        _mark_keyword_success(request, settings)
        current = workspace.current
        current_version = keyword_version(current)
        if current_version != payload.expected_version:
            _audit_keyword_failure(
                db,
                principal=principal,
                action="scraper_keywords_save_refused",
                reason="Active Scraper keywords changed after preview",
                details={
                    "expectedVersion": payload.expected_version,
                    "currentVersion": current_version,
                    "proposedCount": len(proposed),
                    "currentCount": len(current),
                },
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Active keywords changed after this preview. "
                    "Reload the current list and preview again."
                ),
            )

        diff = diff_keywords(current, payload.text)
        upstream_payload = payload.model_copy(
            update={
                "text": "\n".join(diff.proposed),
                "generation_id": generation_id,
            }
        )
        try:
            await operations.save_keywords(upstream_payload)
        except ScraperOperationsError as error:
            _audit_keyword_failure(
                db,
                principal=principal,
                action="scraper_keywords_save_failed",
                reason="Scraper keyword save was refused upstream",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedCount": len(diff.proposed),
                    "enqueueRequested": payload.enqueue,
                    "upstreamStatus": error.status_code,
                },
            )
            if error.status_code == 409:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Active keywords changed after this preview. "
                        "Reload the current list and preview again."
                    ),
                ) from None
            if error.status_code == 422 and error.detail:
                raise HTTPException(
                    status_code=422,
                    detail=error.detail,
                ) from None
            return _native_unavailable(_state(request, settings))

        _mark_keyword_success(request, settings)
        next_version = keyword_version(diff.proposed)
        record_activity(
            db,
            action="scraper_keywords_saved",
            target_type="scraper_keywords",
            target_id="primary",
            actor_id=principal.user_id,
            reason="Reviewed Scraper keyword changes saved",
            details={
                "before": {
                    "version": current_version,
                    "count": len(current),
                },
                "after": {
                    "version": next_version,
                    "count": len(diff.proposed),
                },
                "addedCount": len(diff.added),
                "removedCount": len(diff.removed),
                "unchangedCount": len(diff.unchanged),
                "enqueueRequested": payload.enqueue,
                "generationAccepted": generation_id is not None,
            },
        )
        db.commit()
        return KeywordSaveResult(
            enqueued=payload.enqueue,
            current=diff.proposed,
            version=next_version,
            diff=diff,
        )


@native_router.post(
    "/keywords/generate",
    response_model=KeywordGenerationDraft,
)
async def generate_scraper_keywords(
    payload: KeywordGenerateRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Ask the Scraper generator for a review-only draft."""

    _require_scraper_grant(request, response, principal, settings)
    try:
        draft = await _scraper_operations(
            request,
            settings,
        ).generate_keywords(payload)
    except ScraperOperationsError as error:
        if error.status_code is None:
            details = {
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": "upstream_unavailable",
                "transportError": error.transport_error,
            }
            _audit_keyword_failure(
                db,
                principal=principal,
                action="scraper_keyword_generation_failed",
                reason="Scraper keyword generator could not be reached",
                details=details,
            )
            return _native_unavailable(_state(request, settings))
        message = _safe_generation_error(error.detail)
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Scraper keyword generator returned no review draft",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": message,
            },
        )
        status_code = (
            409
            if message.startswith("Another keyword generation")
            else 422
            if message in {
                "The selected winner is unavailable",
                "AI generation is not configured",
                "Choose a winner before generating adjacent keywords",
            }
            else 503
        )
        raise HTTPException(status_code=status_code, detail=message) from None

    _mark_keyword_success(request, settings)
    record_activity(
        db,
        action="scraper_keyword_generation_created",
        target_type="scraper_keyword_generation",
        target_id=draft.generation_id,
        actor_id=principal.user_id,
        reason="Generated Scraper keywords for human review",
        details={
            "mode": payload.mode,
            "seedKeyword": payload.seed_keyword,
            "keywordCount": len(draft.keywords),
            "excludedCount": draft.excluded_count,
            "activeConfigurationChanged": False,
        },
    )
    db.commit()
    return draft


@native_router.post(
    "/keywords/rollover",
    response_model=KeywordRollover,
)
async def control_scraper_keyword_rollover(
    payload: KeywordRolloverRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    _require_scraper_grant(request, response, principal, settings)
    try:
        rollover = await _scraper_operations(
            request,
            settings,
        ).set_keyword_rollover(payload)
    except ScraperOperationsError as error:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_rollover_failed",
            reason="Automatic Scraper keyword rollover could not be changed",
            details={
                "requestedAction": payload.action,
                "upstreamStatus": error.status_code,
            },
        )
        if error.status_code == 422:
            raise HTTPException(
                status_code=422,
                detail="AI generation is not configured.",
            )
        return _native_unavailable(_state(request, settings))
    rollover = _public_rollover(rollover)
    _mark_keyword_success(request, settings)
    record_activity(
        db,
        action=(
            "scraper_keyword_rollover_enabled"
            if rollover.enabled
            else "scraper_keyword_rollover_disabled"
        ),
        target_type="scraper_keyword_rollover",
        target_id="primary",
        actor_id=principal.user_id,
        reason="Automatic Scraper keyword rollover setting changed",
        details={
            "enabled": rollover.enabled,
            "state": rollover.state,
            "percentComplete": rollover.percent_complete,
        },
    )
    db.commit()
    return rollover


def _runtime_failed(upstream: httpx.Response | None) -> bool:
    return upstream is None or not 200 <= upstream.status_code < 300


def _mark_runtime_success(request: Request, settings: Settings) -> None:
    _state(request, settings).last_success = _workspace_now(request)


async def _runtime_current(
    request: Request,
    settings: Settings,
) -> tuple[
    RuntimeConfiguration,
    list[str],
    list,
] | None:
    upstream = await _raw_native_upstream(
        request,
        settings,
        method="GET",
        path="/configure",
    )
    if _runtime_failed(upstream):
        return None
    try:
        parsed = parse_runtime_workspace(upstream.text)
    except (TypeError, ValueError):
        return None
    _mark_runtime_success(request, settings)
    return parsed


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
        idle_expires_in=SCRAPER_IDLE_SECONDS,
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
    query = urlencode(
        {
            "search": search.strip(),
            "state": normalized_state.lower(),
            "sort": sort,
            "direction": direction,
        }
    )
    upstream = await _raw_native_upstream(
        request,
        settings,
        method="GET",
        path=f"/frag/history/table?{query}",
    )
    proxy_state = _state(request, settings)
    if _runtime_failed(upstream):
        return _campaign_history_unavailable(
            request,
            settings,
            search=search.strip(),
            state=normalized_state,
            sort=sort,
            direction=direction,
        )
    try:
        rows = parse_campaign_history(upstream.text)
    except (TypeError, ValueError):
        return _campaign_history_unavailable(
            request,
            settings,
            search=search.strip(),
            state=normalized_state,
            sort=sort,
            direction=direction,
        )
    _mark_runtime_success(request, settings)
    return CampaignHistory(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        idle_expires_in=SCRAPER_IDLE_SECONDS,
        search=search.strip(),
        state=normalized_state,
        sort=sort,
        direction=direction,
        all_states=sorted(US_STATES),
        rows=rows,
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
        idle_expires_in=SCRAPER_IDLE_SECONDS,
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
    current, all_states, cells = parsed
    return RuntimeWorkspace(
        service_state="connected",
        last_successful_at=_safe_last_success(proxy_state),
        idle_expires_in=SCRAPER_IDLE_SECONDS,
        current=current,
        version=runtime_version(current),
        all_states=all_states,
        cells=cells,
        total_cells=sum(row.cells for row in cells),
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
    current, _, current_cells = parsed
    form = runtime_form(payload.configuration)
    upstream = await _raw_native_upstream(
        request,
        settings,
        method="POST",
        path="/configure/preview",
        form=form,
    )
    if _runtime_failed(upstream):
        return _native_unavailable(_state(request, settings))
    try:
        proposed_cells = parse_cell_effects(upstream.text)
    except (TypeError, ValueError):
        return _native_unavailable(_state(request, settings))
    if (
        len(proposed_cells) != len(payload.configuration.states)
        or {row.state for row in proposed_cells}
        != set(payload.configuration.states)
    ):
        return _native_unavailable(_state(request, settings))
    _mark_runtime_success(request, settings)
    expected_version = runtime_version(current)
    proposed_version = runtime_version(payload.configuration)
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
        effects=calculate_effects(
            current,
            payload.configuration,
            current_cells,
            proposed_cells,
        ),
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
            max_age=SCRAPER_IDLE_SECONDS,
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
        current, _, current_cells = parsed
        current_version = runtime_version(current)
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

        form = runtime_form(payload.configuration)
        effects_upstream = await _raw_native_upstream(
            request,
            settings,
            method="POST",
            path="/configure/preview",
            form=form,
        )
        if _runtime_failed(effects_upstream):
            _audit_runtime_failure(
                db,
                principal=principal,
                action="scraper_runtime_configuration_save_failed",
                reason="Scale runtime effects could not be recalculated before save",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedVersion": proposed_version,
                    "enqueueRequested": payload.enqueue,
                },
            )
            return _native_unavailable(_state(request, settings))
        try:
            proposed_cells = parse_cell_effects(effects_upstream.text)
        except (TypeError, ValueError):
            proposed_cells = []
        if (
            len(proposed_cells) != len(payload.configuration.states)
            or {row.state for row in proposed_cells}
            != set(payload.configuration.states)
        ):
            _audit_runtime_failure(
                db,
                principal=principal,
                action="scraper_runtime_configuration_save_failed",
                reason="Scale runtime effects were invalid before save",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedVersion": proposed_version,
                    "enqueueRequested": payload.enqueue,
                },
            )
            return _native_unavailable(_state(request, settings))

        form["enqueue"] = "true" if payload.enqueue else "false"
        upstream = await _raw_native_upstream(
            request,
            settings,
            method="POST",
            path="/configure/save",
            form=form,
        )
        if _runtime_failed(upstream):
            _audit_runtime_failure(
                db,
                principal=principal,
                action="scraper_runtime_configuration_save_failed",
                reason="Scale runtime configuration save was refused upstream",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedVersion": proposed_version,
                    "enqueueRequested": payload.enqueue,
                    "upstreamStatus": (
                        upstream.status_code if upstream is not None else None
                    ),
                },
            )
            return _native_unavailable(_state(request, settings))

        _mark_runtime_success(request, settings)
        effects = calculate_effects(
            current,
            payload.configuration,
            current_cells,
            proposed_cells,
        )
        revision = ScraperRuntimeConfigurationRevision(
            before_checksum=current_version,
            after_checksum=proposed_version,
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
            version=proposed_version,
            configuration=payload.configuration,
            effects=effects,
            enqueued=payload.enqueue,
        )


def request_is_scraper_origin(
    request: Request,
    settings: Settings,
) -> bool:
    expected = urlsplit(settings.scraper_ops_origin).netloc.lower()
    return bool(expected) and request.headers.get("host", "").lower() == expected


def scraper_handoff_response(
    principal: Principal,
    settings: Settings,
) -> HTMLResponse:
    origin = settings.scraper_ops_origin.rstrip("/")
    if not origin:
        raise HTTPException(
            status_code=503,
            detail="Scraper Operations origin is not configured.",
        )
    handoff = _handoff_serializer(settings).dumps(
        {
            "sub": str(principal.user_id),
            "email": principal.email,
            "role": principal.role,
            "csrf": uuid.uuid4().hex,
        }
    )
    action = html.escape(f"{origin}{MOUNT_PREFIX}/session", quote=True)
    token = html.escape(handoff, quote=True)
    response = HTMLResponse(
        content=f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Opening Scraper Operations</title></head>
<body onload="document.forms[0].submit()">
<main><h1>Opening Scraper Operations</h1>
<form method="post" action="{action}">
<input type="hidden" name="handoff" value="{token}">
<button type="submit">Continue</button>
</form></main></body></html>""",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'unsafe-inline'; "
                f"form-action {origin}; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
        },
    )
    return response


async def accept_scraper_handoff(
    request: Request,
    settings: Settings,
) -> Response:
    if not request_is_scraper_origin(request, settings):
        raise HTTPException(status_code=404, detail="Not found.")
    form = await request.form()
    try:
        payload = _handoff_serializer(settings).loads(
            str(form.get("handoff") or ""),
            max_age=HANDOFF_MAX_AGE_SECONDS,
        )
        principal = Principal(
            user_id=uuid.UUID(str(payload["sub"])),
            email=str(payload["email"]),
            role=str(payload["role"]),
            csrf=str(payload["csrf"]),
        )
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="Scraper Operations handoff expired.",
        ) from None
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

    response = RedirectResponse(f"{MOUNT_PREFIX}/", status_code=303)
    response.set_cookie(
        SCRAPER_SESSION_COOKIE,
        _scraper_serializer(settings).dumps(payload),
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path=MOUNT_PREFIX,
        max_age=settings.session_ttl_seconds,
    )
    return response


def clear_scraper_session(
    request: Request,
    settings: Settings,
) -> Response:
    if not request_is_scraper_origin(request, settings):
        raise HTTPException(status_code=404, detail="Not found.")
    primary = urlsplit(settings.public_base_url)
    expected_origin = f"{primary.scheme}://{primary.netloc}"
    if request.headers.get("origin") != expected_origin:
        raise HTTPException(status_code=403, detail="Invalid logout origin.")
    response = Response(status_code=204)
    response.delete_cookie(
        SCRAPER_SESSION_COOKIE,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path=MOUNT_PREFIX,
    )
    return response


def scraper_principal_from_request(
    request: Request,
    settings: Settings,
) -> Principal:
    token = request.cookies.get(SCRAPER_SESSION_COOKIE, "")
    if not token:
        raise HTTPException(status_code=401, detail="Login required.")
    try:
        payload = _scraper_serializer(settings).loads(
            token,
            max_age=settings.session_ttl_seconds,
        )
        principal = Principal(
            user_id=uuid.UUID(str(payload["sub"])),
            email=str(payload["email"]),
            role=str(payload["role"]),
            csrf=str(payload["csrf"]),
        )
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Session expired.") from None
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-Scraper-CSRF", "")
        if not supplied or not hmac.compare_digest(supplied, principal.csrf):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed.",
            )
    return principal


def _state(request: Request, settings: Settings) -> ScraperProxyState:
    key = (
        settings.scraper_ops_url,
        settings.scraper_ops_user,
        settings.scraper_ops_timeout_seconds,
    )
    current = getattr(request.app.state, "scraper_proxy_state", None)
    if current is None or current.settings_key != key:
        current = ScraperProxyState(settings_key=key)
        request.app.state.scraper_proxy_state = current
    return current


def _upstream_path(path: str) -> str:
    normalized = path.strip("/")
    return f"/{normalized}" if normalized else "/dashboard"


def _mounted_path(path: str) -> str:
    if not path.startswith("/") or path.startswith("//"):
        return path
    return f"{MOUNT_PREFIX}{path}"


def _rewrite_html(
    content: bytes,
    csrf: str,
    admin_url: str,
) -> bytes:
    text = content.decode("utf-8", "replace")
    text = PATH_ATTRIBUTE.sub(
        lambda match: (
            f"{match.group('attribute')}={match.group('quote')}"
            f"{MOUNT_PREFIX}/{match.group('path')}"
        ),
        text,
    )
    headers = html.escape(
        f'{{"X-Scraper-CSRF":"{csrf}"}}',
        quote=True,
    )
    text = re.sub(
        r"<body(?P<attributes>[^>]*)>",
        (
            rf'<body\g<attributes> hx-headers="{headers}">'
            f'<a href="{html.escape(admin_url, quote=True)}" '
            'id="jawnix-admin-return">Jawnix Admin</a>'
        ),
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    return text.encode("utf-8")


def _rewrite_css(content: bytes) -> bytes:
    return content.replace(b"url(/", f"url({MOUNT_PREFIX}/".encode())


def _unavailable(
    request: Request,
    state: ScraperProxyState,
    settings: Settings,
) -> HTMLResponse:
    last_success = (
        state.last_success.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        if state.last_success
        else "Never"
    )
    retry = html.escape(request.url.path, quote=True)
    admin_url = html.escape(
        f"{settings.public_base_url.rstrip('/')}/admin.html",
        quote=True,
    )
    return HTMLResponse(
        status_code=503,
        content=f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scraper Operations unavailable</title>
<style>
body{{font-family:system-ui,sans-serif;background:#f5f7fb;color:#13213a;padding:3rem}}
main{{max-width:42rem;margin:auto;background:white;border:1px solid #d9dfeb;
border-radius:12px;padding:2rem;box-shadow:0 10px 30px #14213d14}}
.actions{{display:flex;gap:.75rem;margin-top:1.5rem}}a{{color:#175cd3}}
.button{{border:1px solid #b8c2d6;border-radius:8px;padding:.65rem 1rem;
text-decoration:none;font-weight:700}}.primary{{background:#13213a;color:white}}
</style></head><body><main>
<p>JAWNIX / ADMIN</p><h1>Scraper Operations unavailable</h1>
<p>The private Scraper connection did not respond. Jawnix feedback,
analytics, and batch administration are still available.</p>
<p><strong>Last successful connection:</strong> {last_success}</p>
<div class="actions"><a class="button primary" href="{retry}">Retry</a>
<a class="button" href="{admin_url}">Back to Jawnix Admin</a></div>
</main></body></html>""",
    )


async def forward_scraper_request(
    request: Request,
    path: str,
    principal: Principal,
    settings: Settings,
) -> Response:
    state = _state(request, settings)
    if (
        not settings.scraper_ops_url
        or not settings.scraper_ops_user
        or not settings.scraper_ops_password
    ):
        return _unavailable(request, state, settings)

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in FORWARDED_REQUEST_HEADERS
    }
    headers["X-Forwarded-Prefix"] = MOUNT_PREFIX
    transport = getattr(
        request.app.state,
        "scraper_proxy_transport",
        None,
    )
    client = httpx.AsyncClient(
        base_url=settings.scraper_ops_url.rstrip("/"),
        auth=httpx.BasicAuth(
            settings.scraper_ops_user,
            settings.scraper_ops_password,
        ),
        timeout=settings.scraper_ops_timeout_seconds,
        follow_redirects=False,
        transport=transport,
    )
    try:
        upstream_request = client.build_request(
            request.method,
            _upstream_path(path),
            params=request.query_params.multi_items(),
            headers=headers,
            content=await request.body(),
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError:
        await client.aclose()
        return _unavailable(request, state, settings)

    if upstream.status_code in {401, 403} or upstream.status_code >= 500:
        await upstream.aclose()
        await client.aclose()
        return _unavailable(request, state, settings)
    state.last_success = datetime.now(timezone.utc)

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in FORWARDED_RESPONSE_HEADERS
    }
    for redirect_header in ("location", "hx-location", "hx-redirect"):
        value = upstream.headers.get(redirect_header)
        if value:
            response_headers[redirect_header] = _mounted_path(value)

    content_type = upstream.headers.get("content-type", "").lower()
    rewritten = "text/html" in content_type or "text/css" in content_type
    if rewritten:
        try:
            content = await upstream.aread()
        except httpx.RequestError:
            await upstream.aclose()
            await client.aclose()
            return _unavailable(request, state, settings)
        await client.aclose()

    if "text/html" in content_type:
        content = _rewrite_html(
            content,
            principal.csrf,
            f"{settings.public_base_url.rstrip('/')}/admin.html",
        )
        response_headers["Content-Security-Policy"] = (
            "default-src 'self' data:; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
    elif "text/css" in content_type:
        content = _rewrite_css(content)
    else:
        async def stream_body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=response_headers,
    )
