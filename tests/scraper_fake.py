"""Pure in-memory fake for the typed ``ScraperOperations`` contract."""

from __future__ import annotations

import json

from jawnix.keyword_generation import (
    KeywordGenerationError,
    KeywordGenerationResult,
)
from jawnix.scraper_coverage import (
    ScraperStateCoverageDetail,
    StateCoverageCard,
    StateGridCell,
    StateGridCoverage,
    StateKeywordActivity,
    StateKeywords,
)
from jawnix.scraper_database import (
    DatabaseBrowsePage,
    DatabaseBusiness,
    DatabaseExport,
    DatabaseNiche,
    DatabaseStateSummary,
    DatabaseTotals,
    ExportRegeneration,
    MultiStateExportRequest,
    ScraperDatabaseStateDetail,
    ScraperDatabaseWorkspace,
    StateExportRequest,
    StoredExport,
)
from jawnix.scraper_keywords import (
    KeywordDiff,
    KeywordRollover,
    KeywordRolloverEventRequest,
    KeywordRolloverRequest,
    KeywordSaveRequest,
    KeywordSaveResult,
    KeywordTextRequest,
    KeywordWinner,
    ScraperKeywordWorkspace,
    diff_keywords,
    keyword_version,
)
from jawnix.scraper_operations import ScraperOperationsError
from jawnix.scraper_operations import ScraperWorkspaceSummary
from jawnix.scraper_monitoring import (
    ControlPipelineRequest,
    ControlPipelineResult,
    RegionData,
    RegionKey,
)
from jawnix.scraper_runtime import (
    ControlCampaignHistory,
    ControlRuntimePreview,
    ControlRuntimeSaveRequest,
    ControlRuntimeSaveResult,
    ControlRuntimeWorkspace,
    HistorySort,
    RuntimeConfiguration,
    RuntimePreviewRequest,
    SortDirection,
    StateCellEffect,
    calculate_effects,
    runtime_version,
)
from jawnix.states import US_STATES


CAPTURED_AT = "2026-07-28T11:59:30+00:00"

STACK_STATUS = {
    "key": "attention",
    "label": "Attention needed",
    "detail": "Only 6 of 8 workers are running",
    "reasons": [
        "Only 6 of 8 workers are running",
        "Queue depth exceeds its warning threshold",
    ],
    "age_seconds": 30,
}

SAMPLE = {
    "captured_at": CAPTURED_AT,
    "cpu_percent": 42.5,
    "load_1": 3.2,
    "memory_used_bytes": 12_884_901_888,
    "memory_total_bytes": 17_179_869_184,
    "memory_percent": 75.0,
    "disk_used_bytes": 429_496_729_600,
    "disk_total_bytes": 536_870_912_000,
    "disk_percent": 80.0,
    "host_uptime_seconds": 950_400,
    "uptime_label": "11d 0h",
    "spool_pending_files": 12,
    "spool_oldest_seconds": 90,
    "spool_age_label": "1m",
    "worker_restarts": 2,
    "expected_workers": 8,
    "running_workers": 6,
    "unhealthy_workers": 1,
    "database_ok": True,
    "dashboard_ok": True,
    "queue_api_ok": True,
    "required_services_ok": False,
    "queue_depth": 812,
    "running_jobs": 6,
    "retryable_jobs": 4,
    "oldest_queue_seconds": 1200,
    "businesses_total": 9_244_326,
    "completed_jobs_total": 1_820_004,
    "empty_rate_1h": 0.18,
    # Upstream carries the raw systemd blob here; Jawnix must not forward it.
    "services": {"docker.service": {"active": "active", "sub": "running"}},
}

SERVICES = [
    {"key": "postgres", "label": "PostgreSQL", "state": "ok", "detail": "healthy"},
    {"key": "dashboard", "label": "Dashboard", "state": "ok", "detail": "healthy"},
    {
        "key": "queue_endpoint",
        "label": "Queue endpoint",
        "state": "ok",
        "detail": "responding",
    },
    {
        "key": "docker.service",
        "label": "Docker engine",
        "state": "ok",
        "detail": "running",
    },
    {
        "key": "gms-enqueue.service",
        "label": "Enqueuer",
        "state": "bad",
        "detail": "failed",
    },
    {
        "key": "external_heartbeat",
        "label": "Better Stack heartbeat",
        "state": "neutral",
        "detail": "not configured",
    },
]

STATS = {
    "businesses": 9_244_326,
    "phone_businesses": 4_588_286,
    "unique_phones": 2_305_025,
    "leads": 1_000_000,
    "available_leads": 750_000,
    "queue_depth": 812,
    "running_jobs": 6,
    "retryable_jobs": 4,
    "oldest_queue_secs": 1200,
    "added_last_hour": 4_215,
    "empty_rate": 0.18,
}

ACTIVITY = {
    "queue_depth": 812,
    "running_jobs": 6,
    "retryable_jobs": 4,
    "jobs_last_minute": 22,
    "jobs_last_five_minutes": 118,
    "results_last_minute": 640,
    "latest_result_at": "2026-07-28T11:59:55+00:00",
    "businesses_last_minute": 310,
    "businesses_last_five_minutes": 1_602,
    "businesses_total": 9_244_326,
    "latest_business_at": "2026-07-28T11:59:58+00:00",
    "latest_keyword": "dentist",
    "latest_state": "PA",
    "latest_result_count": 20,
    "latest_job_at": "2026-07-28T11:59:55+00:00",
    "healthy_workers": 6,
    "latest_write_at": "2026-07-28T11:59:58+00:00",
    "write_age": "2s",
    "write_is_fresh": True,
}

PIPELINE_STATE = {
    "key": "running",
    "label": "Running",
    "detail": "Workers are processing the queue",
}

# The live dashboard sends null for both fields when nothing is paused — a ""
# here once hid a Pydantic rejection that took out the whole monitoring screen
# in production (the null-pause-mode defect fixed alongside issue #70's smoke).
PAUSE_INFO = {"mode": None, "cancelled_jobs": None}

PIPELINE_EVENTS = [
    {
        "job_id": 5_001,
        "created_at": "2026-07-28T11:59:40+00:00",
        "keyword": "dentist",
        "state": "PA",
        "result_count": 20,
        "phone_count": 17,
    },
    {
        "job_id": 5_002,
        "created_at": "2026-07-28T11:59:55+00:00",
        "keyword": "orthodontist",
        "state": "TX",
        "result_count": 18,
        "phone_count": 12,
    },
]

WORKERS = [
    {
        "box_id": "box-1",
        "container_name": "gms-worker-1",
        "reported_at": "2026-07-28T11:59:50+00:00",
        "heartbeat_age": "10s",
        "is_healthy": True,
        "status": "alive",
        "active_jobs": 1,
        "jobs_processed": 4_210,
        "results_per_min": 18.5,
        "current_state": "PA",
        "current_keyword": "dentist",
        "current_job_id": 5_001,
    },
    {
        "box_id": "box-2",
        "container_name": "gms-worker-2",
        "reported_at": "2026-07-28T11:55:00+00:00",
        "heartbeat_age": "5m",
        "is_healthy": False,
        "status": "stale",
        "active_jobs": 0,
        "jobs_processed": 3_900,
        "results_per_min": 0.0,
        "current_state": None,
        "current_keyword": None,
        "current_job_id": None,
    },
]

TRENDS = [
    {
        "label": f"{hour:02d}:00",
        "businesses": 100 + hour,
        "jobs": 20 + hour,
        "queue": 400 + hour,
        "cpu": 40.0,
        "memory": 70.0,
        "businesses_height": 80.0,
        "jobs_height": 60.0,
        "queue_height": 50.0,
    }
    for hour in range(24)
]

INCIDENTS = [
    {
        "checked_at": "2026-07-28T11:30:00+00:00",
        "status": "error",
        "messages": ["Queue depth exceeds its warning threshold"],
    },
    {
        "checked_at": "2026-07-28T10:30:00+00:00",
        "status": "ok",
        "messages": [],
    },
]

TOP_STATES = [
    {"state": "TX", "businesses": 1_204_002},
    {"state": "PA", "businesses": 980_411},
]

KEYWORDS = ["plumbers", "electricians"]
WINNERS = [
    {
        "keyword": "plumbers",
        "phone_businesses": 2_480,
        "businesses": 4_000,
        "posted_cells": 1_000,
        "phones_per_cell": 2.48,
        "phone_rate": 0.62,
        "last_used": "Jul 28",
    },
    {
        "keyword": "roof repair",
        "phone_businesses": 1_200,
        "businesses": 2_000,
        "posted_cells": 600,
        "phones_per_cell": 2.0,
        "phone_rate": 0.6,
        "last_used": "Jul 27",
    },
]

CAMPAIGN_HISTORY = [
    {
        "keyword": "Acoustic Guitar Lessons",
        "state": "OH",
        "cells_posted": 240,
        "first_enqueued": "2026-07-27T13:41:00+00:00",
        "latest_enqueued": "2026-07-29T00:00:00+00:00",
        "campaign_date": "2026-07-29",
    },
    {
        "keyword": "Farm Equipment Dealer",
        "state": "KY",
        "cells_posted": 324,
        "first_enqueued": "2026-07-28T01:49:00+00:00",
        "latest_enqueued": "2026-07-29T00:03:00+00:00",
        "campaign_date": "2026-07-29",
    },
    {
        "keyword": "Plumbers",
        "state": "PA",
        "cells_posted": 110,
        "first_enqueued": "2026-07-20T09:00:00+00:00",
        "latest_enqueued": "2026-07-28T17:30:00+00:00",
        "campaign_date": "2026-07-28",
    },
]

RUNTIME_CONFIGURATION = {
    "states": ["KY", "OH"],
    "settings": {
        "zoom": 15,
        "radius": 10_000.0,
        "depth": 3,
        "lang": "en",
        "fast_mode": True,
        "timeout": 300,
    },
    "queue": {
        "target_depth": 50,
        "target_per_worker": 25,
        "min_target_depth": 25,
        "max_target_depth": 500,
        "batch_size": 100,
        "poll_secs": 5,
        "skip_recent_days": 0,
    },
    "overrides": {},
}

STATE_CELL_COUNTS = {
    "AK": 180,
    "AZ": 99,
    "KY": 324,
    "MO": 143,
    "OH": 240,
    "PA": 220,
    "TX": 500,
}

REGION_PAYLOADS: dict[str, dict] = {
    "overall": {"stack_status": STACK_STATUS},
    "stack": {
        "sample": SAMPLE,
        "stack_status": STACK_STATUS,
        "services": SERVICES,
    },
    "stats": {"stats": STATS},
    "activity": {
        "activity": ACTIVITY,
        "pipeline_state": PIPELINE_STATE,
        "pause_info": PAUSE_INFO,
    },
    "log": {"pipeline_events": PIPELINE_EVENTS},
    "workers": {"workers": WORKERS, "expected_workers": 8},
    "trends": {"trends": TRENDS},
    "incidents": {"incidents": INCIDENTS},
    "top-states": {"top_states": TOP_STATES},
}

STORED_EXPORTS = [
    StoredExport(filename="OH.csv", size_label="42.5 KB"),
    StoredExport(filename="PA.csv", size_label="38.0 KB"),
]

STATE_CARDS = [
    StateCoverageCard(
        state="PA",
        businesses=161_863,
        posted_cells=110,
        total_cells=220,
        active_keywords=25,
        coverage=50,
        status="partial",
    ),
    StateCoverageCard(
        state="OH",
        businesses=136_150,
        posted_cells=240,
        total_cells=240,
        active_keywords=25,
        coverage=100,
        status="covered",
    ),
]

STATE_KEYWORDS = [
    StateKeywordActivity(
        keyword="24 Hour Pharmacy",
        businesses=124,
        posted_cells=110,
        total_cells=220,
        coverage=50,
        empty_rate=0.125,
        last_enqueued="Jul 28, 11:59",
    ),
    StateKeywordActivity(
        keyword="Abatement Service",
        businesses=38,
        posted_cells=0,
        total_cells=220,
        coverage=0,
        empty_rate=0.75,
        last_enqueued=None,
    ),
]

STATE_CELLS = StateGridCoverage(
    cells=[
        StateGridCell(
            index=index,
            cell=f"40.000000,-{80 - (index - 1) * 0.25:.6f}",
            status=status,
        )
        for index, status in enumerate(
            ("posted", "reserved", "failed", "uncovered"),
            start=1,
        )
    ],
    posted=1,
    reserved=1,
    failed=1,
    uncovered=1,
)


def aggregate_payload() -> dict:
    """What ``GET /api/dashboard`` returns: every region merged."""

    payload: dict = {}
    for region in REGION_PAYLOADS.values():
        payload.update(region)
    return payload


def paused_activity(cancelled_jobs: int = 0, mode: str = "drain") -> dict:
    state = (
        {
            "key": "paused",
            "label": "Paused",
            "detail": f"Queue cleared ({cancelled_jobs} cancelled)",
        }
        if mode == "clear"
        else {
            "key": "paused",
            "label": "Paused",
            "detail": "No new scrape jobs will be queued",
        }
    )
    return {
        "activity": {**ACTIVITY, "queue_depth": 0 if mode == "clear" else 812},
        "pipeline_state": state,
        "pause_info": {"mode": mode, "cancelled_jobs": cancelled_jobs},
    }


class GenerationFake:
    """In-memory adapter for Jawnix's high-level generation interface."""

    model = "test/generation-model"

    def __init__(
        self,
        *,
        available: bool = True,
        error: KeywordGenerationError | None = None,
    ) -> None:
        self.available = available
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate_keywords(
        self,
        *,
        mode,
        excluded_keywords,
        seed_keyword=None,
        count=25,
    ) -> KeywordGenerationResult:
        self.calls.append(
            {
                "mode": mode,
                "excluded_keywords": list(excluded_keywords),
                "seed_keyword": seed_keyword,
                "count": count,
            }
        )
        if self.error:
            raise self.error
        terms = [f"Unused Trade {index}" for index in range(1, count + 1)]
        return KeywordGenerationResult(
            terms=terms,
            excluded_count=7,
            candidate_metrics={
                "attemptCount": 2,
                "candidateCount": 32,
                "acceptedCount": count,
                "rejectedCount": 7,
                "rejectionReasons": {"duplicate": 7},
                "attempts": [],
            },
        )

    def propose_niches(self, segments):
        if self.error:
            raise self.error
        return [
            {"id": str(item["id"]), "niche": "Roofing"}
            for item in segments
        ]


class ScraperFake:
    """Records typed calls and implements every in-memory operation."""

    def __init__(
        self,
        *,
        failing: set[str] | None = None,
        coverage_failing: set[str] | None = None,
        offline: bool = False,
        activity_after_write: dict | None = None,
        keywords: list[str] | None = None,
        ai_enabled: bool = True,
        runtime_failing: set[str] | None = None,
    ) -> None:
        self.failing = failing or set()
        self.coverage_failing = coverage_failing or set()
        self.offline = offline
        self.activity_after_write = activity_after_write
        self.keywords = list(keywords or KEYWORDS)
        self.ai_enabled = ai_enabled
        self.runtime_failing = runtime_failing or set()
        self.rollover_enabled = False
        self.rollover_state_override: str | None = None
        self.rollover_events: list[dict[str, object]] = []
        self.keyword_calls: list[str] = []
        self.database_calls: list[tuple[str, object]] = []
        self.coverage_calls: list[str] = []
        self.operation_calls: list[str] = []
        self.monitoring_calls: list[str] = []
        self.pipeline_writes: list[ControlPipelineRequest] = []
        self.keyword_writes: list[dict[str, object]] = []
        self.runtime = json.loads(json.dumps(RUNTIME_CONFIGURATION))
        self.runtime_writes: list[dict[str, object]] = []
        self.history_calls: list[dict[str, str]] = []

    async def workspace_summary(self) -> ScraperWorkspaceSummary:
        self.operation_calls.append("workspace")
        self._operations_failure("workspace")
        return ScraperWorkspaceSummary(
            active_states=list(self.runtime["states"]),
            keyword_count=len(self.keywords),
            business_count=STATS["businesses"],
            pipeline_state="running",
        )

    def _keyword_failure(self) -> None:
        if self.offline:
            raise ScraperOperationsError(transport_error="ConnectError")

    def _rollover_model(self) -> KeywordRollover:
        state = self.rollover_state_override or (
            "working" if self.rollover_enabled else "off"
        )
        return KeywordRollover(
            enabled=self.rollover_enabled,
            state=state,
            label="Current batch active" if self.rollover_enabled else "Off",
            detail=(
                "12 of 20 coverage jobs enqueued"
                if self.rollover_enabled
                else "Manual keyword batches"
            ),
            percent_complete=60,
            posted_jobs=12,
            expected_jobs=20,
            last_status="generated",
            last_event="Jul 28 · 12:00 UTC",
        )

    def _winners(self) -> list[KeywordWinner]:
        return [
            KeywordWinner(rank=rank, **winner)
            for rank, winner in enumerate(WINNERS, 1)
        ]

    async def list_keywords(self) -> ScraperKeywordWorkspace:
        self.keyword_calls.append("list")
        self._keyword_failure()
        return ScraperKeywordWorkspace(
            current=self.keywords,
            version=keyword_version(self.keywords),
            ai_enabled=self.ai_enabled,
            rollover=self._rollover_model(),
            winners=self._winners(),
        )

    async def keyword_winners(self) -> list[KeywordWinner]:
        self.keyword_calls.append("winners")
        self._keyword_failure()
        return self._winners()

    async def preview_keywords(
        self,
        payload: KeywordTextRequest,
    ) -> KeywordDiff:
        self.keyword_calls.append("preview")
        self._keyword_failure()
        diff = diff_keywords(self.keywords, payload.text)
        if not diff.proposed:
            raise ScraperOperationsError(
                status_code=422,
                detail="At least one keyword is required.",
            )
        return diff

    async def save_keywords(
        self,
        payload: KeywordSaveRequest,
    ) -> KeywordSaveResult:
        self.keyword_calls.append("save")
        self._keyword_failure()
        if keyword_version(self.keywords) != payload.expected_version:
            raise ScraperOperationsError(
                status_code=409,
                detail=(
                    "Active keywords changed after this preview. "
                    "Reload the current list and preview again."
                ),
            )
        diff = diff_keywords(self.keywords, payload.text)
        if not diff.proposed:
            raise ScraperOperationsError(
                status_code=422,
                detail="At least one keyword is required.",
            )
        self.keywords = diff.proposed
        self.keyword_writes.append(
            payload.model_dump(exclude={"review_token"}, exclude_none=True)
        )
        return KeywordSaveResult(
            enqueued=payload.enqueue,
            current=self.keywords,
            version=keyword_version(self.keywords),
            diff=diff,
        )

    async def set_keyword_rollover(
        self,
        payload: KeywordRolloverRequest,
    ) -> KeywordRollover:
        self.keyword_calls.append("rollover")
        self._keyword_failure()
        self.rollover_enabled = payload.action == "enable"
        return self._rollover_model()

    async def record_keyword_rollover_event(
        self,
        payload: KeywordRolloverEventRequest,
    ) -> KeywordRollover:
        self.keyword_calls.append("rollover_event")
        self._keyword_failure()
        self.rollover_events.append(payload.model_dump())
        return self._rollover_model()

    def _operations_failure(self, key: str) -> None:
        if self.offline or key in self.coverage_failing:
            raise ScraperOperationsError(transport_error="ConnectError")

    async def database_workspace(
        self,
        *,
        search: str,
        state: str,
        page: int,
    ) -> ScraperDatabaseWorkspace:
        self.database_calls.append(
            ("workspace", {"search": search, "state": state, "page": page})
        )
        self._operations_failure("database")
        return ScraperDatabaseWorkspace(
            totals=DatabaseTotals(
                businesses=9_244_326,
                unique_phones=2_305_025,
            ),
            states=[
                DatabaseStateSummary(
                    state="OH",
                    businesses=136_150,
                    unique_phones=71_204,
                    niches=25,
                ),
                DatabaseStateSummary(
                    state="PA",
                    businesses=161_863,
                    unique_phones=84_110,
                    niches=24,
                ),
            ],
            browse=DatabaseBrowsePage(
                records=[
                    DatabaseBusiness(
                        title="Buckeye Plumbing",
                        phone="(614) 555-0101",
                        website="https://buckeye.example",
                        state="OH",
                        niche="plumbers",
                        last_seen="Jul 28, 11:59",
                    ),
                    DatabaseBusiness(
                        title="Capital Electric",
                        phone="614-555-0101",
                        website=None,
                        state="OH",
                        niche="electricians",
                        last_seen="Jul 28, 11:58",
                    ),
                ],
                search=search,
                state=state.upper(),
                page=page,
                total=51,
                pages=2,
                has_previous=page > 1,
                has_next=page < 2,
            ),
            stored_exports=STORED_EXPORTS,
        )

    async def database_state(
        self,
        state: str,
    ) -> ScraperDatabaseStateDetail:
        self.database_calls.append(("state", state))
        self._operations_failure("database")
        normalized = state.upper()
        return ScraperDatabaseStateDetail(
            state=normalized,
            totals=DatabaseStateSummary(
                state=normalized,
                businesses=136_150,
                unique_phones=71_204,
                niches=2,
            ),
            niches=[
                DatabaseNiche(
                    key="plumbers",
                    label="plumbers",
                    businesses=80_000,
                    unique_phones=42_000,
                ),
                DatabaseNiche(
                    key="__uncategorized__",
                    label="Uncategorized",
                    businesses=56_150,
                    unique_phones=29_204,
                ),
            ],
        )

    async def export_database_state(
        self,
        state: str,
        payload: StateExportRequest,
    ) -> DatabaseExport:
        self.database_calls.append(("export_state", (state, payload)))
        self._operations_failure("database")
        niches = payload.niches
        label = (
            "all"
            if niches is None
            else niches[0]
            if len(niches) == 1
            else f"{len(niches)}-niches"
        )
        normalized = state.upper()
        return DatabaseExport(
            filename=(
                f"{normalized}-{label}-phone-leads-2026-07-29.csv"
            ),
            content=(
                "business_name,phone_number,state\n"
                f"Buckeye Plumbing,6145550101,{normalized}\n"
            ),
        )

    async def export_database_states(
        self,
        payload: MultiStateExportRequest,
    ) -> DatabaseExport:
        self.database_calls.append(("export_states", payload))
        self._operations_failure("database")
        states = [state.upper() for state in payload.states]
        label = "-".join(states) if len(states) <= 4 else f"{len(states)}-states"
        return DatabaseExport(
            filename=f"{label}-phone-leads-2026-07-29.csv",
            content=(
                "business_name,phone_number,state\n"
                + "".join(
                    f"{state} Business,5550000000,{state}\n"
                    for state in states
                )
            ),
        )

    async def stored_database_export(self, filename: str) -> DatabaseExport:
        self.database_calls.append(("stored_export", filename))
        self._operations_failure("database")
        return DatabaseExport(
            filename=filename,
            content="phone,title\n6145550101,Buckeye Plumbing\n",
        )

    async def regenerate_database_exports(
        self,
        state: str,
    ) -> ExportRegeneration:
        self.database_calls.append(("regenerate", state))
        self._operations_failure("database")
        return ExportRegeneration(
            generated=f"{state.upper()}.csv",
            stored_exports=STORED_EXPORTS,
        )

    async def coverage_states(self) -> list[StateCoverageCard]:
        self.coverage_calls.append("states")
        self._operations_failure("cards")
        return STATE_CARDS

    async def coverage_state(
        self,
        state: str,
    ) -> ScraperStateCoverageDetail:
        self.coverage_calls.append(f"{state}:detail")
        self._operations_failure("detail")
        return ScraperStateCoverageDetail(
            state=state.upper(),
            keywords=STATE_KEYWORDS,
            cells=STATE_CELLS,
        )

    async def coverage_state_keywords(self, state: str) -> StateKeywords:
        self.coverage_calls.append(f"{state}:keywords")
        self._operations_failure("keywords")
        return StateKeywords(state=state.upper(), keywords=STATE_KEYWORDS)

    async def coverage_state_cells(self, state: str) -> StateGridCoverage:
        self.coverage_calls.append(f"{state}:cells")
        self._operations_failure("cells")
        return STATE_CELLS

    async def monitoring_dashboard(self) -> RegionData:
        self.monitoring_calls.append("dashboard")
        self._operations_failure("dashboard")
        return RegionData.model_validate(aggregate_payload())

    async def monitoring_region(self, region: RegionKey) -> RegionData:
        self.monitoring_calls.append(region)
        self._operations_failure(region)
        if region in self.failing:
            raise ScraperOperationsError(transport_error="ConnectError")
        if (
            region == "activity"
            and self.activity_after_write
            and self.pipeline_writes
        ):
            payload = self.activity_after_write
        else:
            payload = REGION_PAYLOADS[region]
        return RegionData.model_validate(payload)

    async def control_pipeline(
        self,
        payload: ControlPipelineRequest,
    ) -> ControlPipelineResult:
        self.operation_calls.append("pipeline")
        self._operations_failure("pipeline")
        self.pipeline_writes.append(payload)
        if self.activity_after_write:
            result = self.activity_after_write
        elif payload.action == "pause":
            result = paused_activity(
                mode="clear" if payload.clear_queue else "drain"
            )
        else:
            result = REGION_PAYLOADS["activity"]
        return ControlPipelineResult(
            pipeline_state=result["pipeline_state"],
            cancelled_jobs=int(
                result["pause_info"].get("cancelled_jobs") or 0
            ),
            activity=result["activity"],
            pause_info=result["pause_info"],
        )

    def _runtime_cells(
        self,
        configuration: RuntimeConfiguration,
    ) -> list[StateCellEffect]:
        rows = []
        for state in configuration.states:
            base = STATE_CELL_COUNTS.get(state, 100)
            override = configuration.overrides.get(state)
            cell_size = override.cell_size_km if override else None
            cells = (
                max(1, round(base * 15 / float(cell_size)))
                if cell_size
                else base
            )
            rows.append(StateCellEffect(state=state, cells=cells))
        return rows

    def _runtime_failure(self, key: str) -> None:
        if self.offline or key in self.runtime_failing:
            raise ScraperOperationsError(transport_error="ConnectError")

    async def runtime_workspace(self) -> ControlRuntimeWorkspace:
        self.operation_calls.append("runtime")
        self._runtime_failure("configure")
        current = RuntimeConfiguration.model_validate(self.runtime)
        cells = self._runtime_cells(current)
        return ControlRuntimeWorkspace(
            current=current,
            version=runtime_version(current),
            all_states=sorted(US_STATES),
            cells=cells,
            total_cells=sum(row.cells for row in cells),
        )

    async def preview_runtime(
        self,
        payload: RuntimePreviewRequest,
    ) -> ControlRuntimePreview:
        self.operation_calls.append("runtime_preview")
        self._runtime_failure("preview")
        current = RuntimeConfiguration.model_validate(self.runtime)
        proposed = payload.configuration
        return ControlRuntimePreview(
            configuration=proposed,
            expected_version=runtime_version(current),
            proposed_version=runtime_version(proposed),
            effects=calculate_effects(
                current,
                proposed,
                self._runtime_cells(current),
                self._runtime_cells(proposed),
            ),
        )

    async def save_runtime(
        self,
        payload: ControlRuntimeSaveRequest,
    ) -> ControlRuntimeSaveResult:
        self.operation_calls.append("runtime_save")
        self._runtime_failure("save")
        current = RuntimeConfiguration.model_validate(self.runtime)
        if runtime_version(current) != payload.expected_version:
            raise ScraperOperationsError(
                status_code=409,
                detail=(
                    "Runtime configuration changed after this preview. "
                    "Reload the current settings and preview again."
                ),
            )
        effects = calculate_effects(
            current,
            payload.configuration,
            self._runtime_cells(current),
            self._runtime_cells(payload.configuration),
        )
        self.runtime = payload.configuration.model_dump(mode="json")
        self.runtime_writes.append(payload.model_dump(mode="json"))
        return ControlRuntimeSaveResult(
            version=runtime_version(payload.configuration),
            configuration=payload.configuration,
            effects=effects,
            enqueued=payload.enqueue,
        )

    async def campaign_history(
        self,
        *,
        search: str,
        state: str,
        sort: HistorySort,
        direction: SortDirection,
    ) -> ControlCampaignHistory:
        self.operation_calls.append("history")
        self.history_calls.append(
            {
                "search": search,
                "state": state,
                "sort": sort,
                "direction": direction,
            }
        )
        self._runtime_failure("history")
        normalized_state = state.upper()
        rows = [
            row
            for row in CAMPAIGN_HISTORY
            if (not search or search.casefold() in row["keyword"].casefold())
            and (not normalized_state or normalized_state == row["state"])
        ]
        sort_key = {
            "keyword": "keyword",
            "state": "state",
            "cells_posted": "cells_posted",
            "latest_enqueued": "latest_enqueued",
            "last_enqueued": "campaign_date",
        }[sort]
        rows.sort(
            key=lambda item: item[sort_key],
            reverse=direction == "desc",
        )
        return ControlCampaignHistory(
            search=search,
            state=normalized_state,
            sort=sort,
            direction=direction,
            all_states=sorted(US_STATES),
            rows=rows,
        )
