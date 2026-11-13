from __future__ import annotations

import asyncio

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response

from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..scraper_coverage import (
    STATE_CARDS_REFRESH_SECONDS,
    STATE_CELLS_REFRESH_SECONDS,
    STATE_KEYWORDS_REFRESH_SECONDS,
    CoverageFeed,
    StateCoverageDetail,
    StateCoverageSnapshot,
    StateGridCoverage,
    StateKeywordActivity,
)
from ..scraper_operations import ScraperOperationsError
from ..states import US_STATES
from .core import (
    _scraper_operations,
    _state,
    _workspace_now,
    native_router,
)
from .grants import _require_scraper_grant


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
    try:
        cards = await _scraper_operations(
            request,
            settings,
        ).coverage_states()
    except ScraperOperationsError:
        cards = None
    state = _state(request, settings)
    return StateCoverageSnapshot(
        service_state=(
            "connected" if cards is not None else "unavailable"
        ),
        last_successful_at=state.last_success,
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
    operations = _scraper_operations(request, settings)
    keyword_result, cell_result = await asyncio.gather(
        operations.coverage_state_keywords(state.lower()),
        operations.coverage_state_cells(state.lower()),
        return_exceptions=True,
    )
    keywords = (
        None
        if isinstance(keyword_result, ScraperOperationsError)
        else keyword_result.keywords
    )
    cells = (
        None
        if isinstance(cell_result, ScraperOperationsError)
        else cell_result
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
    try:
        result = await _scraper_operations(
            request,
            settings,
        ).coverage_state_keywords(state.lower())
    except ScraperOperationsError:
        keywords = None
    else:
        keywords = result.keywords
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
    try:
        cells = await _scraper_operations(
            request,
            settings,
        ).coverage_state_cells(state.lower())
    except ScraperOperationsError:
        cells = None
    return _coverage_feed(
        cells,
        refresh_seconds=STATE_CELLS_REFRESH_SECONDS,
        request=request,
    )
