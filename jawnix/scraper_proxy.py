from __future__ import annotations

import asyncio
import html
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    parse_editor,
    parse_feedback_error,
    parse_generation_draft,
    parse_rollover,
    parse_winners,
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
from .states import US_STATES


MOUNT_PREFIX = "/admin/scraper"
SCRAPER_SESSION_COOKIE = "jawnix_scraper_session"
HANDOFF_MAX_AGE_SECONDS = 60
SCRAPER_GRANT_COOKIE = "jawnix_scraper_grant"
SCRAPER_IDLE_SECONDS = 15 * 60
KEYWORD_WRITE_LOCK = asyncio.Lock()
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
    form: dict[str, str] | None = None,
) -> httpx.Response | None:
    if (
        not settings.scraper_ops_url
        or not settings.scraper_ops_user
        or not settings.scraper_ops_password
    ):
        return None
    transport = getattr(request.app.state, "scraper_proxy_transport", None)
    try:
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
            upstream = await client.request(method, path, data=form)
    except httpx.RequestError:
        return None
    return upstream


async def _native_upstream(
    request: Request,
    settings: Settings,
    *,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
) -> httpx.Response | None:
    upstream = await _raw_native_upstream(
        request,
        settings,
        method=method,
        path=path,
        form=form,
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

    upstream = await _native_upstream(
        request,
        settings,
        method="GET",
        path=path,
    )
    if upstream is None:
        return None
    try:
        return parser(upstream.text)
    except (CoverageContractError, UnicodeError):
        return None


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


def _keyword_upstream_failed(
    upstream: httpx.Response | None,
) -> bool:
    return upstream is None or not 200 <= upstream.status_code < 300


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


def _safe_generation_error(upstream: httpx.Response) -> str:
    message = parse_feedback_error(upstream.text)
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


async def _keyword_editor(
    request: Request,
    settings: Settings,
) -> tuple[list[str], bool, KeywordRollover] | None:
    upstream = await _raw_native_upstream(
        request,
        settings,
        method="GET",
        path="/keywords",
    )
    if _keyword_upstream_failed(upstream):
        return None
    try:
        parsed = parse_editor(upstream.text)
    except ValueError:
        return None
    _mark_keyword_success(request, settings)
    return parsed


@native_router.get("/keywords", response_model=KeywordWorkspace)
async def scraper_keyword_workspace(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    """Project every current keyword read without moving ownership to Jawnix."""

    _require_scraper_grant(request, response, principal, settings)
    editor_upstream, winners_upstream = await asyncio.gather(
        _raw_native_upstream(
            request,
            settings,
            method="GET",
            path="/keywords",
        ),
        _raw_native_upstream(
            request,
            settings,
            method="GET",
            path="/keywords/winners",
        ),
    )
    if (
        _keyword_upstream_failed(editor_upstream)
        or _keyword_upstream_failed(winners_upstream)
    ):
        return _native_unavailable(_state(request, settings))
    try:
        current, ai_enabled, rollover = parse_editor(editor_upstream.text)
        winners = parse_winners(winners_upstream.text)
    except ValueError:
        return _native_unavailable(_state(request, settings))
    _mark_keyword_success(request, settings)
    return KeywordWorkspace(
        current=current,
        version=keyword_version(current),
        ai_enabled=ai_enabled,
        rollover=rollover,
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
):
    """Preview is a fresh read, so its version can safely gate the later save."""

    _require_scraper_grant(request, response, principal, settings)
    editor = await _keyword_editor(request, settings)
    if editor is None:
        return _native_unavailable(_state(request, settings))
    current, _, _ = editor
    diff = diff_keywords(current, payload.text)
    if not diff.proposed:
        raise HTTPException(
            status_code=422,
            detail="At least one keyword is required.",
        )
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
        editor = await _keyword_editor(request, settings)
        if editor is None:
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
        current, _, _ = editor
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
        form = {
            "text": "\n".join(diff.proposed),
            "enqueue": "true" if payload.enqueue else "false",
        }
        if generation_id:
            form["generation_id"] = generation_id
        upstream = await _raw_native_upstream(
            request,
            settings,
            method="POST",
            path="/keywords/save",
            form=form,
        )
        if _keyword_upstream_failed(upstream):
            _audit_keyword_failure(
                db,
                principal=principal,
                action="scraper_keywords_save_failed",
                reason="Scraper keyword save was refused upstream",
                details={
                    "expectedVersion": payload.expected_version,
                    "proposedCount": len(diff.proposed),
                    "enqueueRequested": payload.enqueue,
                    "upstreamStatus": (
                        upstream.status_code if upstream is not None else None
                    ),
                },
            )
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
    """Ask the existing generator for a review-only draft."""

    _require_scraper_grant(request, response, principal, settings)
    upstream = await _raw_native_upstream(
        request,
        settings,
        method="POST",
        path="/keywords/generate",
        form={
            "mode": payload.mode,
            "seed_keyword": payload.seed_keyword or "",
        },
    )
    if upstream is None:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Scraper keyword generator could not be reached",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": "upstream_unavailable",
            },
        )
        return _native_unavailable(_state(request, settings))
    if upstream.status_code == 200:
        message = _safe_generation_error(upstream)
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
        raise HTTPException(status_code=status_code, detail=message)
    if upstream.status_code != status.HTTP_303_SEE_OTHER:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Scraper keyword generator returned an invalid response",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "upstreamStatus": upstream.status_code,
            },
        )
        return _native_unavailable(_state(request, settings))

    location = urlsplit(upstream.headers.get("location", ""))
    draft_values = parse_qs(location.query).get("draft", [])
    try:
        if location.path != "/keywords" or len(draft_values) != 1:
            raise ValueError
        generation_id = str(uuid.UUID(draft_values[0]))
    except ValueError:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Scraper keyword generator returned an invalid draft",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "outcome": "invalid_draft_location",
            },
        )
        return _native_unavailable(_state(request, settings))

    draft_upstream = await _raw_native_upstream(
        request,
        settings,
        method="GET",
        path=f"/keywords?draft={generation_id}",
    )
    if _keyword_upstream_failed(draft_upstream):
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Scraper keyword review draft could not be loaded",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "generationId": generation_id,
            },
        )
        return _native_unavailable(_state(request, settings))
    try:
        draft = parse_generation_draft(
            draft_upstream.text,
            generation_id=generation_id,
            mode=payload.mode,
            seed_keyword=payload.seed_keyword,
        )
    except ValueError:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_generation_failed",
            reason="Scraper keyword review draft was invalid",
            details={
                "mode": payload.mode,
                "seedKeyword": payload.seed_keyword,
                "generationId": generation_id,
            },
        )
        return _native_unavailable(_state(request, settings))

    _mark_keyword_success(request, settings)
    record_activity(
        db,
        action="scraper_keyword_generation_created",
        target_type="scraper_keyword_generation",
        target_id=generation_id,
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
    upstream = await _raw_native_upstream(
        request,
        settings,
        method="POST",
        path="/keywords/auto-rollover",
        form={"action": payload.action},
    )
    if _keyword_upstream_failed(upstream):
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_rollover_failed",
            reason="Automatic Scraper keyword rollover could not be changed",
            details={
                "requestedAction": payload.action,
                "upstreamStatus": (
                    upstream.status_code if upstream is not None else None
                ),
            },
        )
        if upstream is not None and upstream.status_code == 422:
            raise HTTPException(
                status_code=422,
                detail="AI generation is not configured.",
            )
        return _native_unavailable(_state(request, settings))
    try:
        rollover = parse_rollover(upstream.text)
    except ValueError:
        _audit_keyword_failure(
            db,
            principal=principal,
            action="scraper_keyword_rollover_failed",
            reason="Automatic Scraper keyword rollover returned invalid status",
            details={"requestedAction": payload.action},
        )
        return _native_unavailable(_state(request, settings))
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
