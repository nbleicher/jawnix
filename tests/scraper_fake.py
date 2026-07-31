"""A controlled Scraper fake that answers the real projection contract.

Every payload here is shaped exactly like the upstream dashboard's own context
for that region — same keys, same derived fields — so a test passing against
this fake is evidence about the real contract rather than about the fake. The
per-region split mirrors ``/api/dashboard/{region}``.
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import parse_qs

import httpx

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
    KeywordGenerateRequest,
    KeywordGenerationDraft,
    KeywordRollover,
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
        "first_enqueued": "Jul 27, 13:41",
        "latest_enqueued": "Jul 29, 00:00",
        "campaign_date": "Jul 29, 2026",
    },
    {
        "keyword": "Farm Equipment Dealer",
        "state": "KY",
        "cells_posted": 324,
        "first_enqueued": "Jul 28, 01:49",
        "latest_enqueued": "Jul 29, 00:03",
        "campaign_date": "Jul 29, 2026",
    },
    {
        "keyword": "Plumbers",
        "state": "PA",
        "cells_posted": 110,
        "first_enqueued": "Jul 20, 09:00",
        "latest_enqueued": "Jul 28, 17:30",
        "campaign_date": "Jul 28, 2026",
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


class ScraperFake:
    """Records every upstream call and answers the projection contract.

    ``failing`` names regions that should behave as if the upstream region is
    down, which is how the per-region isolation tests make one panel fail
    without touching the other eight.
    """

    def __init__(
        self,
        *,
        failing: set[str] | None = None,
        coverage_failing: set[str] | None = None,
        offline: bool = False,
        activity_after_write: dict | None = None,
        keywords: list[str] | None = None,
        ai_enabled: bool = True,
        generation_error: str | None = None,
        generation_timeout: bool = False,
        runtime_failing: set[str] | None = None,
    ) -> None:
        self.failing = failing or set()
        self.coverage_failing = coverage_failing or set()
        self.offline = offline
        self.activity_after_write = activity_after_write
        self.keywords = list(keywords or KEYWORDS)
        self.ai_enabled = ai_enabled
        self.generation_error = generation_error
        self.generation_timeout = generation_timeout
        self.runtime_failing = runtime_failing or set()
        self.rollover_enabled = False
        self.drafts: dict[str, list[str]] = {}
        self.keyword_calls: list[str] = []
        self.database_calls: list[tuple[str, object]] = []
        self.coverage_calls: list[str] = []
        self.calls: list[httpx.Request] = []
        self.writes: list[bytes] = []
        self.keyword_writes: list[dict[str, object]] = []
        self.runtime = json.loads(json.dumps(RUNTIME_CONFIGURATION))
        self.runtime_writes: list[dict[str, list[str]]] = []

    def _keyword_failure(self) -> None:
        if self.offline:
            raise ScraperOperationsError(transport_error="ConnectError")

    def _rollover_model(self) -> KeywordRollover:
        return KeywordRollover(
            enabled=self.rollover_enabled,
            state="working" if self.rollover_enabled else "off",
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
        if payload.generation_id and payload.generation_id not in self.drafts:
            raise ScraperOperationsError(
                status_code=422,
                detail="Invalid keyword generation.",
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

    async def generate_keywords(
        self,
        payload: KeywordGenerateRequest,
    ) -> KeywordGenerationDraft:
        self.keyword_calls.append("generate")
        self._keyword_failure()
        if self.generation_timeout:
            raise ScraperOperationsError(transport_error="ReadTimeout")
        if self.generation_error:
            status_code = (
                409
                if self.generation_error.startswith("Another keyword generation")
                else 503
            )
            raise ScraperOperationsError(
                status_code=status_code,
                detail=self.generation_error,
            )
        if not self.ai_enabled:
            raise ScraperOperationsError(
                status_code=422,
                detail="AI generation is not configured",
            )
        if (
            payload.mode == "adjacent"
            and (payload.seed_keyword or "").casefold()
            not in {item["keyword"].casefold() for item in WINNERS}
        ):
            raise ScraperOperationsError(
                status_code=422,
                detail="The selected winner is unavailable",
            )
        generation_id = str(uuid.uuid4())
        keywords = [f"Unused Service {index}" for index in range(1, 26)]
        self.drafts[generation_id] = keywords
        label = (
            f"keywords adjacent to {payload.seed_keyword}"
            if payload.mode == "adjacent"
            else "broad local-business keywords"
        )
        return KeywordGenerationDraft(
            generation_id=generation_id,
            mode=payload.mode,
            seed_keyword=payload.seed_keyword,
            keywords=keywords,
            excluded_count=7,
            notice=(
                f"Draft ready: 25 {label}. Review below; nothing has been "
                "saved or enqueued. 7 candidates were filtered."
            ),
        )

    async def set_keyword_rollover(
        self,
        payload: KeywordRolloverRequest,
    ) -> KeywordRollover:
        self.keyword_calls.append("rollover")
        self._keyword_failure()
        if payload.action == "enable" and not self.ai_enabled:
            raise ScraperOperationsError(
                status_code=422,
                detail="AI generation is not configured.",
            )
        self.rollover_enabled = payload.action == "enable"
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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if self.offline:
            raise httpx.ConnectError("scraper unreachable", request=request)

        if path == "/dashboard/pipeline":
            self.writes.append(request.content)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text="<section>ok</section>",
            )
        if path == "/frag/history/table":
            if "history" in self.runtime_failing:
                return httpx.Response(503, text="history unavailable")
            return self._html(self._history_table(request))
        if path == "/configure" and request.method == "GET":
            if "configure" in self.runtime_failing:
                return httpx.Response(503, text="configure unavailable")
            return self._html(self._configure_page())
        if path == "/configure/preview":
            if "preview" in self.runtime_failing:
                return httpx.Response(503, text="preview unavailable")
            proposed = self._runtime_from_form(request)
            return self._html(self._runtime_preview(proposed))
        if path == "/configure/save":
            if "save" in self.runtime_failing:
                return httpx.Response(503, text="save unavailable")
            proposed = self._runtime_from_form(request)
            self.runtime = proposed
            self.runtime_writes.append(self._multi_form(request))
            return self._html(self._runtime_preview(proposed, saved=True))
        if path == "/api/dashboard":
            return self._json(aggregate_payload())
        if path.startswith("/api/dashboard/"):
            region = path.rsplit("/", 1)[-1]
            if region in self.failing:
                return httpx.Response(503, text="region unavailable")
            if region == "activity" and self.activity_after_write and self.writes:
                return self._json(self.activity_after_write)
            payload = REGION_PAYLOADS.get(region)
            if payload is None:
                return httpx.Response(404, text="unknown region")
            return self._json(payload)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html><body>GMS/OPS ready</body></html>",
        )

    @staticmethod
    def _form(request: httpx.Request) -> dict[str, str]:
        return {
            key: values[-1]
            for key, values in parse_qs(request.content.decode()).items()
        }

    @staticmethod
    def _multi_form(request: httpx.Request) -> dict[str, list[str]]:
        return parse_qs(request.content.decode(), keep_blank_values=True)

    @staticmethod
    def _html(document: str) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text=document,
        )

    def _history_table(self, request: httpx.Request) -> str:
        search = request.url.params.get("search", "").casefold()
        state = request.url.params.get("state", "").upper()
        sort = request.url.params.get("sort", "last_enqueued")
        reverse = request.url.params.get("direction", "desc") != "asc"
        rows = [
            row
            for row in CAMPAIGN_HISTORY
            if (not search or search in row["keyword"].casefold())
            and (not state or state == row["state"])
        ]
        sort_key = {
            "keyword": "keyword",
            "state": "state",
            "cells_posted": "cells_posted",
            "latest_enqueued": "latest_enqueued",
            "last_enqueued": "campaign_date",
        }.get(sort, "campaign_date")
        rows.sort(key=lambda item: item[sort_key], reverse=reverse)
        body = "".join(
            f"""
            <tr>
              <td><strong>{row["keyword"]}</strong></td>
              <td><span class="state-code small">{row["state"]}</span></td>
              <td>{row["cells_posted"]}</td>
              <td>{row["first_enqueued"]}</td>
              <td>{row["latest_enqueued"]}</td>
              <td>{row["campaign_date"]}</td>
            </tr>
            """
            for row in rows
        )
        if not body:
            body = (
                '<tr><td colspan="6" class="table-empty">'
                "No campaign history</td></tr>"
            )
        return f"""
        <div class="table-wrap"><table>
          <thead><tr>
            <th>Keyword</th><th>State</th><th>Cells posted</th>
            <th>First enqueue</th><th>Latest enqueue</th>
            <th>Campaign date</th>
          </tr></thead>
          <tbody>{body}</tbody>
        </table></div>
        """

    def _runtime_cells(self, configuration: dict) -> list[tuple[str, int]]:
        rows = []
        for state in configuration["states"]:
            base = STATE_CELL_COUNTS.get(state, 100)
            cell_size = (
                configuration["overrides"]
                .get(state, {})
                .get("cell_size_km")
            )
            cells = (
                max(1, round(base * 15 / float(cell_size)))
                if cell_size
                else base
            )
            rows.append((state, cells))
        return rows

    def _runtime_preview(
        self,
        configuration: dict,
        *,
        saved: bool = False,
    ) -> str:
        notice = (
            '<div class="notice success"><span>Configuration saved</span></div>'
            if saved
            else ""
        )
        rows = "".join(
            (
                '<div><span class="state-code small">'
                f"{state}</span><strong>{cells:,}</strong></div>"
            )
            for state, cells in self._runtime_cells(configuration)
        )
        return f"""
        {notice}
        <div class="section-head"><div>
          <p class="eyebrow">Computed</p><h2>Cell count</h2>
        </div></div>
        <div class="preview-list">{rows}</div>
        """

    def _configure_page(self) -> str:
        state_inputs = "".join(
            (
                '<label><input type="checkbox" name="states" '
                f'value="{state.lower()}"'
                f'{" checked" if state in self.runtime["states"] else ""}>'
                f"<span>{state}</span></label>"
            )
            for state in sorted(US_STATES)
        )
        settings = self.runtime["settings"]
        queue = self.runtime["queue"]
        overrides = "".join(
            f"""
            <div class="override-row"><strong>{state}</strong>
              <label>Cell size (km)<input type="number"
                name="cell_size_km_{state.lower()}"
                value="{self.runtime["overrides"].get(state, {}).get("cell_size_km", "")}">
              </label>
              <label>Zoom<input type="number"
                name="zoom_{state.lower()}"
                value="{self.runtime["overrides"].get(state, {}).get("zoom", "")}">
              </label>
            </div>
            """
            for state in self.runtime["states"]
        )
        return f"""
        <html><body>
          <form>
            <div class="state-picker">{state_inputs}</div>
            <input name="zoom" value="{settings["zoom"]}">
            <input name="radius" value="{settings["radius"]}">
            <input name="depth" value="{settings["depth"]}">
            <input name="lang" value="{settings["lang"]}">
            <input name="fast_mode" type="checkbox"
              {"checked" if settings["fast_mode"] else ""}>
            <input name="timeout" value="{settings["timeout"]}">
            <input name="target_depth" value="{queue["target_depth"]}">
            <input name="target_per_worker"
              value="{queue["target_per_worker"]}">
            <input name="min_target_depth"
              value="{queue["min_target_depth"]}">
            <input name="max_target_depth"
              value="{queue["max_target_depth"]}">
            <input name="batch_size" value="{queue["batch_size"]}">
            <input name="poll_secs" value="{queue["poll_secs"]}">
            <input name="skip_recent_days"
              value="{queue["skip_recent_days"]}">
            <div class="override-grid">{overrides}</div>
            {self._runtime_preview(self.runtime)}
          </form>
        </body></html>
        """

    def _runtime_from_form(self, request: httpx.Request) -> dict:
        form = self._multi_form(request)

        def one(name: str, default: object) -> str:
            values = form.get(name)
            return values[-1] if values else str(default)

        states = [value.upper() for value in form.get("states", [])]
        overrides = {}
        for state in states:
            code = state.lower()
            values = {}
            cell_size = one(f"cell_size_km_{code}", "").strip()
            zoom = one(f"zoom_{code}", "").strip()
            if cell_size:
                values["cell_size_km"] = float(cell_size)
            if zoom:
                values["zoom"] = int(float(zoom))
            if values:
                overrides[state] = values
        return {
            "states": states,
            "settings": {
                "zoom": int(float(one("zoom", 15))),
                "radius": float(one("radius", 10_000)),
                "depth": int(float(one("depth", 3))),
                "lang": one("lang", "en"),
                "fast_mode": one("fast_mode", "") == "on",
                "timeout": int(float(one("timeout", 300))),
            },
            "queue": {
                "target_depth": int(float(one("target_depth", 50))),
                "target_per_worker": int(
                    float(one("target_per_worker", 25))
                ),
                "min_target_depth": int(
                    float(one("min_target_depth", 25))
                ),
                "max_target_depth": int(
                    float(one("max_target_depth", 500))
                ),
                "batch_size": int(float(one("batch_size", 100))),
                "poll_secs": int(float(one("poll_secs", 5))),
                "skip_recent_days": int(
                    float(one("skip_recent_days", 0))
                ),
            },
            "overrides": overrides,
        }

    @staticmethod
    def _json(payload: dict) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload).encode(),
        )
