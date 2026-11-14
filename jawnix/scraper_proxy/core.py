from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from itsdangerous import URLSafeTimedSerializer

from ..config import Settings
from ..keyword_generation import GenerationProvider, build_generation_provider
from ..scraper_operations import HTTPScraperOperations, ScraperOperations


logger = logging.getLogger(__name__)

SCRAPER_GRANT_COOKIE = "jawnix_scraper_grant"
#: How long one Scraper health read answers Today. Today re-reads on every
#: visit and after every decision; the Scraper is asked at most this often.
HEALTH_CACHE_SECONDS = 30
#: How long a reviewed keyword or runtime preview may wait before its save.
#: It bounds how stale the reviewed numbers can be, not the Scraper session.
REVIEW_MAX_AGE_SECONDS = 15 * 60
KEYWORD_WRITE_LOCK = asyncio.Lock()
RUNTIME_CONFIGURATION_WRITE_LOCK = asyncio.Lock()


@dataclass
class ScraperProxyState:
    settings_key: tuple[str, str, float]
    last_success: datetime | None = None
    #: Today's health line, shared by every administrator for
    #: ``HEALTH_CACHE_SECONDS``. Only ever read after the reader's own grant
    #: check, so the cache never shows health to a sign-in without one.
    health: dict | None = None
    health_read_at: datetime | None = None
    health_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


native_router = APIRouter(
    prefix="/api/admin/scraper",
    tags=["scraper-operations"],
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


def _mark_operations_success(request: Request, settings: Settings) -> None:
    _state(request, settings).last_success = _workspace_now(request)


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


def _generation_provider(
    request: Request,
    settings: Settings,
) -> GenerationProvider:
    override = getattr(
        request.app.state,
        "keyword_generation_provider",
        None,
    )
    if override is not None:
        return override
    return build_generation_provider(
        settings,
        transport=getattr(
            request.app.state,
            "keyword_generation_transport",
            None,
        ),
    )


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
