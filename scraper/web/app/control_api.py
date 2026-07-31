"""Authenticated JSON operations for the headless Scraper control process."""

from __future__ import annotations

import asyncio
import json
import math
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import queries
from .auth import require_control_token
from .contracts import (
    ActivateSourceSegments,
    AdjacentKeywordRequest,
    AdjacentKeywordResponse,
    CampaignHistory,
    CampaignHistoryRow,
    CoverageStates,
    DashboardRegion,
    DashboardSnapshot,
    DatabaseBrowsePage,
    DatabaseBusiness,
    DatabaseExport,
    DatabaseNiche,
    DatabaseStateDetail,
    DatabaseStateSummary,
    DatabaseTotals,
    DatabaseWorkspace,
    DatasetPublication,
    ExportRegeneration,
    HistorySort,
    KeywordDiff,
    KeywordGenerateRequest,
    KeywordGenerationDraft,
    KeywordRollover,
    KeywordRolloverRequest,
    KeywordSaveRequest,
    KeywordSaveResult,
    KeywordTextRequest,
    KeywordWinner,
    KeywordWinners,
    KeywordWorkspace,
    MultiStateExportRequest,
    NicheProposalRequest,
    NicheProposalResponse,
    PipelineControlRequest,
    PipelineControlResult,
    RuntimeConfiguration,
    RuntimePreview,
    RuntimePreviewRequest,
    RuntimeSaveRequest,
    RuntimeSaveResult,
    RuntimeWorkspace,
    SortDirection,
    SourceSegment,
    SourceSegments,
    StateCellEffect,
    StateCoverageCard,
    StateCoverageDetail,
    StateExportRequest,
    StateGridCell,
    StateGridCoverage,
    StateKeywordActivity,
    StateKeywords,
    StoredExport,
    WorkspaceSummary,
    keyword_diff,
    keyword_version,
    runtime_effects,
    runtime_version,
)
from .db import fetch, fetchrow, fetchval
from .keyword_generator import GenerationError
from .monitoring import build_trends
from .routers import dashboard as dashboard_legacy
from .routers import database as database_legacy
from .routers import history as history_legacy
from .routers import keywords as keywords_legacy
from .routers import states as states_legacy

router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_control_token)],
)
KEYWORD_WRITE_LOCK = asyncio.Lock()
RUNTIME_WRITE_LOCK = asyncio.Lock()


def bridge(request: Request):
    return request.app.state.control


def _stored_exports(request: Request) -> list[StoredExport]:
    values = []
    for item in database_legacy.export_files(request):
        size = int(item["size"])
        if size < 1024:
            label = f"{size} B"
        elif size < 1024 * 1024:
            label = f"{size / 1024:.1f} KB"
        else:
            label = f"{size / (1024 * 1024):.1f} MB"
        values.append(StoredExport(filename=item["name"], size_label=label))
    return values


async def _keyword_rollover(request: Request) -> KeywordRollover:
    context = await keywords_legacy.rollover_context(request)
    status = context["rollover"]
    state = context["rollover_state"]
    progress = context["rollover_progress"]
    last_event = status.get("last_event_at")
    return KeywordRollover(
        enabled=bool(status["enabled"]),
        state=state["key"],
        label=state["label"],
        detail=state["detail"],
        percent_complete=int(progress["percent_complete"]),
        posted_jobs=int(progress["posted_jobs"] or 0),
        expected_jobs=int(progress["expected_jobs"] or 0),
        last_status=status.get("last_status"),
        last_event=last_event.isoformat() if last_event else None,
    )


async def _keyword_winners(request: Request) -> list[KeywordWinner]:
    rows = await fetch(request, queries.KEYWORD_WINNERS)
    return [
        KeywordWinner(
            rank=index,
            keyword=row["keyword"],
            phone_businesses=int(row["phone_businesses"] or 0),
            businesses=int(row["businesses"] or 0),
            posted_cells=int(row["posted_cells"] or 0),
            phones_per_cell=float(row["phones_per_cell"] or 0),
            phone_rate=float(row["phone_rate"] or 0),
            last_used=row["last_used"],
        )
        for index, row in enumerate(rows, 1)
    ]


@router.get("/workspace", response_model=WorkspaceSummary)
async def workspace_summary(request: Request):
    stats, activity = await asyncio.gather(
        dashboard_legacy.stats_context(request),
        dashboard_legacy.activity_context(request),
    )
    return WorkspaceSummary(
        active_states=[state.upper() for state in bridge(request).active_states()],
        keyword_count=len(bridge(request).load_keywords()),
        business_count=int(stats["stats"]["businesses"] or 0),
        pipeline_state=activity["pipeline_state"]["key"],
    )


@router.get("/keywords", response_model=KeywordWorkspace)
async def keyword_workspace(request: Request):
    current = bridge(request).load_keywords()
    rollover, winners = await asyncio.gather(
        _keyword_rollover(request),
        _keyword_winners(request),
    )
    return KeywordWorkspace(
        current=current,
        version=keyword_version(current),
        ai_enabled=request.app.state.settings.keyword_ai_enabled,
        rollover=rollover,
        winners=winners,
    )


@router.get("/keywords/winners", response_model=KeywordWinners)
async def keyword_winners(request: Request):
    return KeywordWinners(winners=await _keyword_winners(request))


@router.post("/keywords/preview", response_model=KeywordDiff)
async def preview_keywords(payload: KeywordTextRequest, request: Request):
    proposed = bridge(request).parse_keywords(payload.text)
    if not proposed:
        raise HTTPException(status_code=422, detail="At least one keyword is required.")
    return keyword_diff(bridge(request).load_keywords(), proposed)


@router.post("/keywords/save", response_model=KeywordSaveResult)
async def save_keywords(payload: KeywordSaveRequest, request: Request):
    control = bridge(request)
    proposed = control.parse_keywords(payload.text)
    if not proposed:
        raise HTTPException(status_code=422, detail="At least one keyword is required.")
    generation_id: UUID | None = None
    if payload.generation_id:
        try:
            generation_id = UUID(payload.generation_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid keyword generation.") from error

    async with KEYWORD_WRITE_LOCK:
        current = control.load_keywords()
        current_version = keyword_version(current)
        if current_version != payload.expected_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Active keywords changed after this preview. "
                    "Reload the current list and preview again."
                ),
            )
        diff = keyword_diff(current, proposed)
        await control.atomic_write(
            request.app.state.settings.keywords_path,
            "\n".join(proposed) + "\n",
        )
        if payload.enqueue:
            await control.trigger_enqueue()
        if generation_id is not None:
            accepted = await fetchval(
                request,
                queries.ACCEPT_KEYWORD_GENERATION,
                str(generation_id),
                rw=True,
            )
            if accepted is None:
                raise HTTPException(status_code=422, detail="Invalid keyword generation.")
        return KeywordSaveResult(
            enqueued=payload.enqueue,
            current=proposed,
            version=keyword_version(proposed),
            diff=diff,
        )


@router.post("/keywords/generate", response_model=KeywordGenerationDraft)
async def generate_keywords(payload: KeywordGenerateRequest, request: Request):
    rows = await fetch(request, queries.USED_KEYWORDS)
    used = {str(row["keyword"]).strip() for row in rows if row["keyword"]}
    used.update(bridge(request).load_keywords())
    if payload.mode == "adjacent":
        winners = await _keyword_winners(request)
        if not payload.seed_keyword or payload.seed_keyword.casefold() not in {
            item.keyword.casefold() for item in winners
        }:
            raise HTTPException(status_code=422, detail="The selected winner is unavailable")
    try:
        result = await request.app.state.keyword_generator.generate(
            payload.mode,
            used,
            payload.seed_keyword,
        )
    except GenerationError as error:
        message = str(error)
        status = 409 if message.startswith("Another keyword generation") else 422 if message in {
            "AI generation is not configured",
            "Choose a winner before generating adjacent keywords",
        } else 503
        raise HTTPException(status_code=status, detail=message) from None
    generation_id = uuid4()
    await fetchval(
        request,
        queries.INSERT_KEYWORD_GENERATION,
        str(generation_id),
        payload.mode,
        payload.seed_keyword,
        request.app.state.settings.openrouter_model,
        json.dumps(result.keywords),
        result.excluded_count,
        rw=True,
    )
    await fetch(request, queries.CLEANUP_KEYWORD_GENERATIONS, rw=True)
    label = (
        f"keywords adjacent to {payload.seed_keyword}"
        if payload.mode == "adjacent"
        else "broad local-business keywords"
    )
    notice = (
        f"Draft ready: 25 {label}. Review below; nothing has been saved or "
        f"enqueued. {result.excluded_count} candidates were filtered."
    )
    return KeywordGenerationDraft(
        generation_id=str(generation_id),
        mode=payload.mode,
        seed_keyword=payload.seed_keyword,
        keywords=result.keywords,
        excluded_count=result.excluded_count,
        notice=notice,
    )


@router.post("/keywords/rollover", response_model=KeywordRollover)
async def set_keyword_rollover(payload: KeywordRolloverRequest, request: Request):
    if payload.action == "enable" and not request.app.state.settings.keyword_ai_enabled:
        raise HTTPException(status_code=422, detail="AI generation is not configured.")
    await fetchval(
        request,
        queries.SET_AUTO_KEYWORD_ROLLOVER,
        "true" if payload.action == "enable" else "false",
        rw=True,
    )
    return await _keyword_rollover(request)


def _browse_page(context: dict) -> DatabaseBrowsePage:
    def safe_website(value: str | None) -> str | None:
        if not value:
            return None
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else None

    records = [
        DatabaseBusiness(
            title=row["title"] or "",
            phone=row["phone"] or None,
            website=safe_website(row["website"]),
            state=row["state"] or None,
            niche=row["keyword"] or None,
            last_seen=row["last_seen"],
        )
        for row in context["businesses"]
    ]
    page = int(context["page"])
    total = int(context["business_total"] or 0)
    return DatabaseBrowsePage(
        records=records,
        search=context["search"].strip(),
        state=context["state"].upper(),
        page=page,
        total=total,
        pages=max(1, math.ceil(total / 50)),
        has_previous=page > 1,
        has_next=bool(context["has_next"]),
    )


@router.get("/database", response_model=DatabaseWorkspace)
async def database_workspace(
    request: Request,
    search: str = Query(default="", max_length=500),
    state: str = Query(default="", max_length=2),
    page: int = Query(default=1, ge=1),
):
    normalized_state = state.strip().lower()
    if normalized_state and normalized_state not in bridge(request).grid.STATE_CONFIG:
        raise HTTPException(status_code=404, detail="Unknown database state")
    context = await database_legacy.database_context(
        request, search.strip(), normalized_state, page
    )
    return DatabaseWorkspace(
        totals=DatabaseTotals(**dict(context["database_totals"])),
        states=[DatabaseStateSummary(**dict(item)) for item in context["database_states"]],
        browse=_browse_page(context),
        stored_exports=_stored_exports(request),
    )


@router.get("/database/states/{state}", response_model=DatabaseStateDetail)
async def database_state(state: str, request: Request):
    normalized = await database_legacy.validate_database_state(request, state)
    summaries = await database_legacy.database_states(request)
    summary = next(item for item in summaries if item["state"].lower() == normalized)
    niches = await database_legacy.state_niches(request, normalized)
    return DatabaseStateDetail(
        state=normalized.upper(),
        totals=DatabaseStateSummary(**dict(summary)),
        niches=[
            DatabaseNiche(
                key=item["niche_key"],
                label=item["niche"],
                businesses=int(item["businesses"]),
                unique_phones=int(item["unique_phones"]),
            )
            for item in niches
        ],
    )


async def _csv_content(
    request: Request,
    states: list[str],
    niches: list[str] | None = None,
) -> str:
    chunks = ["business_name,phone_number,state\n"]
    for state in states:
        async for chunk in database_legacy.stream_database_csv(
            request, state, niches, include_header=False
        ):
            chunks.append(chunk)
    return "".join(chunks)


@router.post("/database/exports/state/{state}", response_model=DatabaseExport)
async def export_database_state(
    state: str, payload: StateExportRequest, request: Request
):
    normalized = await database_legacy.validate_database_state(request, state)
    niches = None if payload.niches is None else list(dict.fromkeys(payload.niches))
    if niches == []:
        raise HTTPException(status_code=422, detail="Select at least one niche")
    if niches is not None:
        available = {
            row["niche_key"]
            for row in await database_legacy.state_niches(request, normalized)
        }
        if any(niche not in available for niche in niches):
            raise HTTPException(status_code=400, detail="Unknown niche for this state")
    return DatabaseExport(
        filename=database_legacy.export_filename(normalized, niches),
        content=await _csv_content(request, [normalized], niches),
    )


@router.post("/database/exports/states", response_model=DatabaseExport)
async def export_database_states(
    payload: MultiStateExportRequest, request: Request
):
    states = list(dict.fromkeys(item.strip().lower() for item in payload.states))
    states = [
        await database_legacy.validate_database_state(request, state)
        for state in states
    ]
    return DatabaseExport(
        filename=database_legacy.bulk_export_filename(states),
        content=await _csv_content(request, states),
    )


@router.get(
    "/database/exports/stored/{filename}", response_model=DatabaseExport
)
async def stored_database_export(filename: str, request: Request):
    target = database_legacy.resolve_download(request, filename)
    return DatabaseExport(filename=target.name, content=target.read_text(encoding="utf-8"))


@router.post(
    "/database/exports/{state}/regenerate", response_model=ExportRegeneration
)
async def regenerate_database_exports(state: str, request: Request):
    normalized = state.strip().lower()
    if normalized not in bridge(request).grid.STATE_CONFIG:
        raise HTTPException(status_code=404, detail="Unknown state")

    def generate() -> None:
        control = bridge(request)
        connection = control.export_leads.get_conn(
            request.app.state.settings.database_url
        )
        try:
            with connection.cursor() as cursor:
                control.export_leads.do_by_state(
                    cursor, request.app.state.settings.exports_dir
                )
        finally:
            connection.close()

    await asyncio.to_thread(generate)
    filename = f"{normalized.upper()}.csv"
    database_legacy.resolve_download(request, filename)
    return ExportRegeneration(generated=filename, stored_exports=_stored_exports(request))


def _coverage_status(posted: int, total: int) -> str:
    if total and posted >= total:
        return "covered"
    if posted:
        return "partial"
    return "uncovered"


async def _coverage_cards(request: Request) -> list[StateCoverageCard]:
    return [
        StateCoverageCard(
            state=card["state"].upper(),
            businesses=int(card["businesses"] or 0),
            posted_cells=int(card["posted_cells"] or 0),
            total_cells=int(card["total_cells"] or 0),
            active_keywords=int(card["active_keywords"] or 0),
            coverage=int(card["coverage"] or 0),
            status=_coverage_status(card["posted_cells"], card["total_cells"]),
        )
        for card in await states_legacy.state_cards(request)
    ]


async def _state_keywords(
    request: Request, state: str
) -> list[StateKeywordActivity]:
    return [
        StateKeywordActivity(**dict(item))
        for item in await states_legacy.keyword_rows(request, state)
    ]


async def _state_cells(request: Request, state: str) -> StateGridCoverage:
    rows = await states_legacy.cell_rows(request, state)
    cells = [
        StateGridCell(index=index, cell=item["cell"], status=item["status"])
        for index, item in enumerate(rows, 1)
    ]
    counts = {
        status: sum(item.status == status for item in cells)
        for status in ("posted", "reserved", "failed", "uncovered")
    }
    return StateGridCoverage(cells=cells, **counts)


@router.get("/coverage", response_model=CoverageStates)
async def coverage_states(request: Request):
    return CoverageStates(states=await _coverage_cards(request))


@router.get("/coverage/{state}", response_model=StateCoverageDetail)
async def coverage_state(state: str, request: Request):
    normalized = states_legacy.validate_state(request, state)
    keywords, cells = await asyncio.gather(
        _state_keywords(request, normalized),
        _state_cells(request, normalized),
    )
    return StateCoverageDetail(
        state=normalized.upper(), keywords=keywords, cells=cells
    )


@router.get("/coverage/{state}/keywords", response_model=StateKeywords)
async def coverage_state_keywords(state: str, request: Request):
    normalized = states_legacy.validate_state(request, state)
    return StateKeywords(
        state=normalized.upper(), keywords=await _state_keywords(request, normalized)
    )


@router.get("/coverage/{state}/cells", response_model=StateGridCoverage)
async def coverage_state_cells(state: str, request: Request):
    normalized = states_legacy.validate_state(request, state)
    return await _state_cells(request, normalized)


async def _dashboard_region(request: Request, region: DashboardRegion) -> dict:
    if region == "stats":
        return await dashboard_legacy.stats_context(request)
    if region == "workers":
        return await dashboard_legacy.workers_context(request)
    if region == "activity":
        return await dashboard_legacy.activity_context(request)
    if region == "log":
        return await dashboard_legacy.pipeline_log_context(request)
    if region == "trends":
        return {"trends": build_trends(await fetch(request, queries.STACK_TRENDS))}
    if region == "top-states":
        return {"top_states": [dict(row) for row in await fetch(request, queries.TOP_STATES)]}
    stack = await dashboard_legacy.stack_context(request)
    if region == "overall":
        return {"stack_status": stack["stack_status"]}
    if region == "incidents":
        return {"incidents": stack["incidents"]}
    return {
        key: stack[key] for key in ("sample", "stack_status", "services")
    }


@router.get(
    "/dashboard",
    response_model=DashboardSnapshot,
    response_model_exclude_none=True,
)
async def monitoring_dashboard(request: Request):
    regions = await asyncio.gather(
        *(
            _dashboard_region(request, region)
            for region in (
                "stats",
                "stack",
                "workers",
                "activity",
                "log",
                "trends",
                "top-states",
            )
        )
    )
    combined = {}
    for region in regions:
        combined.update(region)
    stack = await dashboard_legacy.stack_context(request)
    combined["incidents"] = stack["incidents"]
    return DashboardSnapshot.model_validate(combined)


@router.get(
    "/dashboard/{region}",
    response_model=DashboardSnapshot,
    response_model_exclude_none=True,
)
async def monitoring_dashboard_region(region: DashboardRegion, request: Request):
    return DashboardSnapshot.model_validate(await _dashboard_region(request, region))


@router.post("/pipeline", response_model=PipelineControlResult)
async def control_pipeline(payload: PipelineControlRequest, request: Request):
    control = bridge(request)
    if payload.action == "pause":
        mode = "clear" if payload.clear_queue else "drain"
        await control.set_pipeline_paused(True, mode=mode)
        if payload.clear_queue:
            cancelled = int(
                await fetchval(
                    request, queries.CANCEL_PENDING_SCRAPE_JOBS, rw=True
                )
                or 0
            )
            await control.set_pipeline_paused(
                True, mode=mode, cancelled_jobs=cancelled
            )
    else:
        await control.set_pipeline_paused(False)
    context = await dashboard_legacy.activity_context(request)
    return PipelineControlResult(
        pipeline_state=context["pipeline_state"],
        cancelled_jobs=int(context["pause_info"]["cancelled_jobs"] or 0),
        activity=context["activity"],
        pause_info=context["pause_info"],
    )


def _runtime_configuration(request: Request) -> RuntimeConfiguration:
    raw = bridge(request).load_active_config()
    return RuntimeConfiguration(
        states=raw["states"],
        settings=raw["settings"],
        queue=raw["queue"],
        overrides=raw["overrides"],
    )


def _runtime_cells(
    request: Request, configuration: RuntimeConfiguration
) -> list[StateCellEffect]:
    invalid = sorted(
        state
        for state in configuration.states
        if state.lower() not in bridge(request).grid.STATE_CONFIG
    )
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unknown states: {', '.join(invalid)}")
    legacy = {
        "states": [state.lower() for state in configuration.states],
        "settings": configuration.settings.model_dump(mode="json"),
        "queue": configuration.queue.model_dump(mode="json"),
        "overrides": {
            state.lower(): override.model_dump(mode="json", exclude_none=True)
            for state, override in configuration.overrides.items()
        },
    }
    return [
        StateCellEffect(
            state=state,
            cells=len(bridge(request).state_cells(state.lower(), legacy)),
        )
        for state in configuration.states
    ]


def _runtime_workspace(request: Request) -> RuntimeWorkspace:
    current = _runtime_configuration(request)
    cells = _runtime_cells(request, current)
    return RuntimeWorkspace(
        current=current,
        version=runtime_version(current),
        all_states=sorted(state.upper() for state in bridge(request).grid.STATE_CONFIG),
        cells=cells,
        total_cells=sum(item.cells for item in cells),
    )


@router.get("/runtime", response_model=RuntimeWorkspace)
async def runtime_workspace(request: Request):
    return _runtime_workspace(request)


@router.post("/runtime/preview", response_model=RuntimePreview)
async def preview_runtime(payload: RuntimePreviewRequest, request: Request):
    current = _runtime_configuration(request)
    return RuntimePreview(
        configuration=payload.configuration,
        expected_version=runtime_version(current),
        proposed_version=runtime_version(payload.configuration),
        effects=runtime_effects(
            current,
            payload.configuration,
            _runtime_cells(request, current),
            _runtime_cells(request, payload.configuration),
        ),
    )


@router.post("/runtime/save", response_model=RuntimeSaveResult)
async def save_runtime(payload: RuntimeSaveRequest, request: Request):
    async with RUNTIME_WRITE_LOCK:
        current = _runtime_configuration(request)
        current_version = runtime_version(current)
        if current_version != payload.expected_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Runtime configuration changed after this preview. "
                    "Reload the current settings and preview again."
                ),
            )
        effects = runtime_effects(
            current,
            payload.configuration,
            _runtime_cells(request, current),
            _runtime_cells(request, payload.configuration),
        )
        existing = bridge(request).load_active_config()
        serialized = {
            "states": [state.lower() for state in payload.configuration.states],
            "settings": payload.configuration.settings.model_dump(mode="json"),
            "queue": payload.configuration.queue.model_dump(mode="json"),
            "overrides": {
                state.lower(): override.model_dump(mode="json", exclude_none=True)
                for state, override in payload.configuration.overrides.items()
            },
            "api_base": existing.get("api_base", "http://localhost:8080"),
        }
        await bridge(request).atomic_write(
            request.app.state.settings.active_states_path,
            yaml.safe_dump(serialized, sort_keys=False),
            yaml_check=True,
        )
        if payload.enqueue:
            await bridge(request).trigger_enqueue()
        return RuntimeSaveResult(
            version=runtime_version(payload.configuration),
            configuration=payload.configuration,
            effects=effects,
            enqueued=payload.enqueue,
        )


@router.get("/history", response_model=CampaignHistory)
async def campaign_history(
    request: Request,
    search: str = Query(default="", max_length=200),
    state: str = Query(default="", max_length=2),
    sort: HistorySort = "last_enqueued",
    direction: SortDirection = "desc",
):
    normalized_state = state.strip().lower()
    if normalized_state and normalized_state not in bridge(request).grid.STATE_CONFIG:
        raise HTTPException(status_code=422, detail="Unknown state.")
    rows = await history_legacy.history_rows(
        request, search.strip(), normalized_state, sort, direction
    )
    return CampaignHistory(
        search=search.strip(),
        state=normalized_state.upper(),
        sort=sort,
        direction=direction,
        all_states=sorted(state.upper() for state in bridge(request).grid.STATE_CONFIG),
        rows=[
            CampaignHistoryRow(
                keyword=row["keyword"],
                state=row["state"],
                cells_posted=int(row["cells_posted"] or 0),
                first_enqueued=row["first_enqueued"],
                latest_enqueued=row["latest_enqueued"],
                campaign_date=row["last_enqueued"],
            )
            for row in rows
        ],
    )


def _source_segments_response(
    request: Request, version: int, segments: list, *, scheduled: bool | None = None
) -> SourceSegments:
    return SourceSegments(
        version=version,
        checksum=bridge(request).source_segments.contract_checksum(version, segments),
        segments=[
            SourceSegment.model_validate(
                {
                    key: value
                    for key, value in item.__dict__.items()
                    if key != "version"
                }
            )
            for item in segments
        ],
        scheduled=scheduled,
    )


@router.get(
    "/source-segments",
    response_model=SourceSegments,
    response_model_exclude_none=True,
)
async def source_segments(request: Request):
    version, segments = bridge(request).load_source_segments()
    return _source_segments_response(request, version, segments)


@router.post(
    "/source-segments/activate",
    response_model=SourceSegments,
    response_model_exclude_none=True,
)
async def activate_source_segments(
    payload: ActivateSourceSegments, request: Request
):
    control = bridge(request)
    try:
        segments = [
            control.source_segments.SourceSegment.from_mapping(
                item.model_dump(), payload.version
            )
            for item in payload.segments
        ]
        calculated = control.source_segments.contract_checksum(
            payload.version, segments
        )
        if calculated != payload.checksum:
            raise ValueError("Source Segment checksum does not match.")
        await control.write_source_segments(payload.version, segments)
        await control.trigger_enqueue()
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return _source_segments_response(
        request, payload.version, segments, scheduled=True
    )


@router.get("/source-segments/publication", response_model=DatasetPublication)
async def latest_publication(request: Request):
    row = await fetchrow(
        request,
        """
        SELECT publication_date, committed_at, business_count, lead_count,
               latest_job_at, checksum
        FROM scraper_dataset_publications
        ORDER BY publication_date DESC
        LIMIT 1
        """,
    )
    return DatasetPublication(
        status="committed" if row else "empty",
        committed_at=row["committed_at"] if row else None,
        publication_date=row["publication_date"] if row else None,
        business_count=int(row["business_count"]) if row else 0,
        lead_count=int(row["lead_count"]) if row else 0,
        latest_job_at=row["latest_job_at"] if row else None,
        checksum=row["checksum"] if row else None,
    )


@router.post(
    "/source-segments/niche-proposals", response_model=NicheProposalResponse
)
async def propose_source_niches(
    payload: NicheProposalRequest, request: Request
):
    values = [item.model_dump() for item in payload.segments]
    try:
        proposals = await request.app.state.keyword_generator.propose_niches(values)
    except GenerationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    return NicheProposalResponse(proposals=proposals)


@router.post(
    "/source-segments/adjacent-keywords",
    response_model=AdjacentKeywordResponse,
)
async def propose_adjacent_keywords(
    payload: AdjacentKeywordRequest, request: Request
):
    try:
        result = await request.app.state.keyword_generator.generate(
            "adjacent", payload.excluded_keywords, payload.seed_keyword
        )
    except GenerationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    return AdjacentKeywordResponse(
        seed_keyword=payload.seed_keyword,
        keywords=result.keywords[: payload.count],
    )
