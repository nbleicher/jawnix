"""Typed wire contract for the private Scraper control service.

This is the only module that defines request and response bodies exposed over
the acquisition host's WireGuard interface.  Jawnix imports the semantics of
these operations through its HTTP adapter; host paths and implementation
details stay private to this process.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Health(ContractModel):
    status: Literal["ok"] = "ok"


class WorkspaceSummary(ContractModel):
    status: Literal["ok"] = "ok"
    active_states: list[str]
    keyword_count: int = Field(ge=0)
    business_count: int = Field(ge=0)
    pipeline_state: Literal["pausing", "paused", "running", "stopped"]


class KeywordRollover(ContractModel):
    enabled: bool
    state: Literal["off", "working", "draining", "ready"]
    label: str
    detail: str
    percent_complete: int = Field(ge=0, le=100)
    posted_jobs: int | None = None
    expected_jobs: int | None = None
    last_status: Literal["generated", "error"] | None = None
    last_event: str | None = None


class KeywordWinner(ContractModel):
    rank: int = Field(ge=1)
    keyword: str
    phone_businesses: int = Field(ge=0)
    businesses: int = Field(ge=0)
    posted_cells: int = Field(ge=0)
    phones_per_cell: float = Field(ge=0)
    phone_rate: float = Field(ge=0)
    last_used: date | datetime | str


class KeywordWinners(ContractModel):
    winners: list[KeywordWinner]


class KeywordWorkspace(ContractModel):
    current: list[str]
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    ai_enabled: bool
    rollover: KeywordRollover
    winners: list[KeywordWinner]


class KeywordTextRequest(ContractModel):
    text: str = Field(max_length=1_000_000)


class KeywordDiff(ContractModel):
    proposed: list[str]
    added: list[str]
    removed: list[str]
    unchanged: list[str]
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class KeywordSaveRequest(KeywordTextRequest):
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    enqueue: bool = False
    generation_id: str | None = None

    @field_validator("generation_id")
    @classmethod
    def normalize_generation_id(cls, value: str | None) -> str | None:
        value = value.strip() if value else ""
        return value or None


class KeywordSaveResult(ContractModel):
    saved: bool = True
    enqueued: bool
    current: list[str]
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    diff: KeywordDiff


class KeywordGenerateRequest(ContractModel):
    mode: Literal["broad", "adjacent"] = "broad"
    seed_keyword: str | None = Field(default=None, max_length=200)

    @field_validator("seed_keyword")
    @classmethod
    def normalize_seed(cls, value: str | None) -> str | None:
        value = value.strip() if value else ""
        return value or None


class KeywordGenerationDraft(ContractModel):
    generation_id: str
    mode: Literal["broad", "adjacent"]
    seed_keyword: str | None = None
    keywords: list[str]
    excluded_count: int = Field(ge=0)
    notice: str


class KeywordRolloverRequest(ContractModel):
    action: Literal["enable", "disable"]


class DatabaseTotals(ContractModel):
    businesses: int = Field(ge=0)
    unique_phones: int = Field(ge=0)


class DatabaseStateSummary(DatabaseTotals):
    state: str = Field(pattern=r"^[A-Z]{2}$")
    niches: int = Field(ge=0)


class DatabaseBusiness(ContractModel):
    title: str
    phone: str | None = None
    website: str | None = None
    state: str | None = None
    niche: str | None = None
    last_seen: datetime


class DatabaseBrowsePage(ContractModel):
    records: list[DatabaseBusiness]
    search: str
    state: str
    page: int = Field(ge=1)
    page_size: int = Field(default=50, ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
    has_previous: bool
    has_next: bool


class StoredExport(ContractModel):
    filename: str
    size_label: str


class DatabaseWorkspace(ContractModel):
    totals: DatabaseTotals
    states: list[DatabaseStateSummary]
    browse: DatabaseBrowsePage
    stored_exports: list[StoredExport]


class DatabaseNiche(DatabaseTotals):
    key: str
    label: str


class DatabaseStateDetail(ContractModel):
    state: str = Field(pattern=r"^[A-Z]{2}$")
    totals: DatabaseStateSummary
    niches: list[DatabaseNiche]


class StateExportRequest(ContractModel):
    niches: list[str] | None = None


class MultiStateExportRequest(ContractModel):
    states: list[str] = Field(min_length=1, max_length=50)


class DatabaseExport(ContractModel):
    filename: str
    media_type: Literal["text/csv"] = "text/csv"
    content: str


class ExportRegeneration(ContractModel):
    generated: str
    stored_exports: list[StoredExport]


CoverageStatus = Literal["covered", "partial", "uncovered"]
CellStatus = Literal["posted", "reserved", "failed", "uncovered"]


class StateCoverageCard(ContractModel):
    state: str
    businesses: int = Field(ge=0)
    posted_cells: int = Field(ge=0)
    total_cells: int = Field(ge=0)
    active_keywords: int = Field(ge=0)
    coverage: int = Field(ge=0, le=100)
    status: CoverageStatus


class StateKeywordActivity(ContractModel):
    keyword: str
    businesses: int = Field(ge=0)
    posted_cells: int = Field(ge=0)
    total_cells: int = Field(ge=0)
    coverage: int = Field(ge=0, le=100)
    empty_rate: float = Field(ge=0)
    last_enqueued: datetime | None = None


class StateGridCell(ContractModel):
    index: int = Field(ge=1)
    cell: str
    status: CellStatus


class StateGridCoverage(ContractModel):
    cells: list[StateGridCell]
    posted: int = Field(ge=0)
    reserved: int = Field(ge=0)
    failed: int = Field(ge=0)
    uncovered: int = Field(ge=0)


class CoverageStates(ContractModel):
    states: list[StateCoverageCard]


class StateKeywords(ContractModel):
    state: str
    keywords: list[StateKeywordActivity]


class StateCoverageDetail(ContractModel):
    state: str
    keywords: list[StateKeywordActivity]
    cells: StateGridCoverage


class StackStatus(ContractModel):
    key: Literal["operational", "idle", "attention", "stale"]
    label: str
    detail: str
    reasons: list[str] = Field(default_factory=list)
    age_seconds: int | None = None


class ServiceRow(ContractModel):
    key: str
    label: str
    state: Literal["ok", "neutral", "bad"]
    detail: str


class StackSample(BaseModel):
    model_config = ConfigDict(extra="ignore")

    captured_at: datetime
    cpu_percent: float | None = None
    load_1: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    memory_percent: float | None = None
    disk_used_bytes: int | None = None
    disk_total_bytes: int | None = None
    disk_percent: float | None = None
    host_uptime_seconds: int | None = None
    uptime_label: str | None = None
    spool_pending_files: int | None = None
    spool_oldest_seconds: int | None = None
    spool_age_label: str | None = None
    worker_restarts: int | None = None
    expected_workers: int | None = None
    running_workers: int | None = None
    unhealthy_workers: int | None = None
    database_ok: bool | None = None
    dashboard_ok: bool | None = None
    queue_api_ok: bool | None = None
    required_services_ok: bool | None = None
    queue_depth: int | None = None
    running_jobs: int | None = None
    retryable_jobs: int | None = None
    oldest_queue_seconds: int | None = None
    businesses_total: int | None = None
    completed_jobs_total: int | None = None
    empty_rate_1h: float | None = None


class DashboardStats(BaseModel):
    model_config = ConfigDict(extra="ignore")

    businesses: int = 0
    phone_businesses: int = 0
    unique_phones: int = 0
    leads: int = 0
    available_leads: int = 0
    queue_depth: int = 0
    running_jobs: int = 0
    retryable_jobs: int = 0
    oldest_queue_secs: int = 0
    added_last_hour: int = 0
    empty_rate: float = 0.0


class PipelineState(ContractModel):
    key: Literal["pausing", "paused", "running", "stopped"]
    label: str
    detail: str


class PauseInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str = ""
    cancelled_jobs: int = 0

    @field_validator("mode", mode="before")
    @classmethod
    def null_mode(cls, value: object) -> object:
        return "" if value is None else value

    @field_validator("cancelled_jobs", mode="before")
    @classmethod
    def null_cancelled_jobs(cls, value: object) -> object:
        return 0 if value is None else value


class PipelineActivity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    queue_depth: int = 0
    running_jobs: int = 0
    retryable_jobs: int = 0
    jobs_last_minute: int = 0
    jobs_last_five_minutes: int = 0
    results_last_minute: int = 0
    latest_result_at: datetime | None = None
    businesses_last_minute: int = 0
    businesses_last_five_minutes: int = 0
    businesses_total: int = 0
    latest_business_at: datetime | None = None
    latest_keyword: str | None = None
    latest_state: str | None = None
    latest_result_count: int | None = None
    latest_job_at: datetime | None = None
    healthy_workers: int = 0
    latest_write_at: datetime | None = None
    write_age: str = "never"
    write_is_fresh: bool = False


class PipelineEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: int
    created_at: datetime
    keyword: str
    state: str
    result_count: int | None = None
    phone_count: int | None = None


class Worker(BaseModel):
    model_config = ConfigDict(extra="ignore")

    box_id: str
    container_name: str
    reported_at: datetime
    heartbeat_age: str
    is_healthy: bool
    status: str
    active_jobs: int | None = None
    jobs_processed: int | None = None
    results_per_min: float | None = None
    current_state: str | None = None
    current_keyword: str | None = None
    current_job_id: int | None = None


class TrendBucket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    businesses: int = 0
    jobs: int = 0
    queue: int = 0
    cpu: float = 0.0
    memory: float = 0.0
    businesses_height: float = 0.0
    jobs_height: float = 0.0
    queue_height: float = 0.0


class Incident(BaseModel):
    model_config = ConfigDict(extra="ignore")

    checked_at: datetime
    status: str
    messages: list[str] = Field(default_factory=list)


class TopState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    state: str
    businesses: int = 0


class DashboardSnapshot(ContractModel):
    stack_status: StackStatus | None = None
    sample: StackSample | None = None
    services: list[ServiceRow] | None = None
    stats: DashboardStats | None = None
    activity: PipelineActivity | None = None
    pipeline_state: PipelineState | None = None
    pause_info: PauseInfo | None = None
    pipeline_events: list[PipelineEvent] | None = None
    workers: list[Worker] | None = None
    expected_workers: int | None = None
    trends: list[TrendBucket] | None = None
    incidents: list[Incident] | None = None
    top_states: list[TopState] | None = None


DashboardRegion = Literal[
    "overall",
    "stack",
    "stats",
    "activity",
    "log",
    "workers",
    "trends",
    "incidents",
    "top-states",
]


class PipelineControlRequest(ContractModel):
    action: Literal["pause", "resume"]
    clear_queue: bool = False

    @model_validator(mode="after")
    def only_pause_can_clear(self):
        if self.clear_queue and self.action != "pause":
            raise ValueError("Only a pause can clear the queue.")
        return self


class PipelineControlResult(ContractModel):
    ok: bool = True
    pipeline_state: PipelineState
    cancelled_jobs: int = Field(ge=0)
    activity: PipelineActivity
    pause_info: PauseInfo


class RuntimeSettings(ContractModel):
    zoom: int = Field(default=15, ge=1, le=21)
    radius: float = Field(default=10_000, ge=100, le=100_000)
    depth: int = Field(default=3, ge=1, le=100)
    lang: str = Field(default="en", max_length=10)
    fast_mode: bool = False
    timeout: int = Field(default=300, ge=1, le=300)

    @field_validator("lang")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return value.strip() or "en"


class QueueSettings(ContractModel):
    target_depth: int = Field(default=50, ge=1, le=10_000)
    target_per_worker: int = Field(default=25, ge=1, le=100)
    min_target_depth: int = Field(default=25, ge=1, le=10_000)
    max_target_depth: int = Field(default=500, ge=1, le=100_000)
    batch_size: int = Field(default=100, ge=1, le=10_000)
    poll_secs: int = Field(default=5, ge=5, le=3_600)
    skip_recent_days: int = Field(default=0, ge=0, le=365)

    @model_validator(mode="after")
    def ordered_depth_bounds(self):
        if self.min_target_depth > self.max_target_depth:
            raise ValueError("Minimum queue depth cannot exceed maximum queue depth.")
        return self


class StateOverride(ContractModel):
    cell_size_km: float | None = Field(default=None, ge=1, le=500)
    zoom: int | None = Field(default=None, ge=1, le=21)

    @model_validator(mode="after")
    def has_value(self):
        if self.cell_size_km is None and self.zoom is None:
            raise ValueError("A state override must change at least one value.")
        return self


class RuntimeConfiguration(ContractModel):
    states: list[str] = Field(default_factory=list)
    settings: RuntimeSettings = Field(default_factory=RuntimeSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    overrides: dict[str, StateOverride] = Field(default_factory=dict)

    @field_validator("states")
    @classmethod
    def normalize_states(cls, values: list[str]) -> list[str]:
        states = [value.strip().upper() for value in values]
        if len(states) != len(set(states)):
            raise ValueError("Active states cannot contain duplicates.")
        if any(not re.fullmatch(r"[A-Z]{2}", state) for state in states):
            raise ValueError("Active states must be two-letter codes.")
        return sorted(states)

    @field_validator("overrides")
    @classmethod
    def normalize_overrides(
        cls, values: dict[str, StateOverride]
    ) -> dict[str, StateOverride]:
        normalized: dict[str, StateOverride] = {}
        for raw_state, override in values.items():
            state = raw_state.strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", state):
                raise ValueError(f"Unknown state override: {state}")
            if state in normalized:
                raise ValueError(f"Duplicate state override: {state}")
            normalized[state] = override
        return dict(sorted(normalized.items()))

    @model_validator(mode="after")
    def overrides_are_active(self):
        inactive = sorted(set(self.overrides) - set(self.states))
        if inactive:
            raise ValueError(
                "State overrides require an active state: " + ", ".join(inactive)
            )
        return self


class FieldBounds(ContractModel):
    minimum: float
    maximum: float
    step: float = 1


class RuntimeBounds(ContractModel):
    runtime: dict[str, FieldBounds]
    queue: dict[str, FieldBounds]
    override: dict[str, FieldBounds]
    language_max_length: int = 10


RUNTIME_BOUNDS = RuntimeBounds(
    runtime={
        "zoom": FieldBounds(minimum=1, maximum=21),
        "radius": FieldBounds(minimum=100, maximum=100_000),
        "depth": FieldBounds(minimum=1, maximum=100),
        "timeout": FieldBounds(minimum=1, maximum=300),
    },
    queue={
        "target_depth": FieldBounds(minimum=1, maximum=10_000),
        "target_per_worker": FieldBounds(minimum=1, maximum=100),
        "min_target_depth": FieldBounds(minimum=1, maximum=10_000),
        "max_target_depth": FieldBounds(minimum=1, maximum=100_000),
        "batch_size": FieldBounds(minimum=1, maximum=10_000),
        "poll_secs": FieldBounds(minimum=5, maximum=3_600),
        "skip_recent_days": FieldBounds(minimum=0, maximum=365),
    },
    override={
        "cell_size_km": FieldBounds(minimum=1, maximum=500, step=0.5),
        "zoom": FieldBounds(minimum=1, maximum=21),
    },
)


class StateCellEffect(ContractModel):
    state: str
    cells: int = Field(ge=0)


class RuntimeEffects(ContractModel):
    cells: list[StateCellEffect]
    current_total_cells: int = Field(ge=0)
    proposed_total_cells: int = Field(ge=0)
    total_cell_delta: int
    states_added: list[str]
    states_removed: list[str]
    runtime_changes: list[str]
    queue_changes: list[str]
    override_changes: list[str]


class RuntimeWorkspace(ContractModel):
    current: RuntimeConfiguration
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    all_states: list[str]
    cells: list[StateCellEffect]
    total_cells: int = Field(ge=0)
    bounds: RuntimeBounds = RUNTIME_BOUNDS


class RuntimePreviewRequest(ContractModel):
    configuration: RuntimeConfiguration


class RuntimePreview(ContractModel):
    configuration: RuntimeConfiguration
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposed_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    effects: RuntimeEffects


class RuntimeSaveRequest(RuntimePreviewRequest):
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    enqueue: bool = False


class RuntimeSaveResult(ContractModel):
    saved: bool = True
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: RuntimeConfiguration
    effects: RuntimeEffects
    enqueued: bool


HistorySort = Literal[
    "keyword", "state", "last_enqueued", "cells_posted", "latest_enqueued"
]
SortDirection = Literal["asc", "desc"]


class CampaignHistoryRow(ContractModel):
    keyword: str
    state: str
    cells_posted: int = Field(ge=0)
    first_enqueued: datetime | None = None
    latest_enqueued: datetime | None = None
    campaign_date: date


class CampaignHistory(ContractModel):
    search: str = ""
    state: str = ""
    sort: HistorySort = "last_enqueued"
    direction: SortDirection = "desc"
    all_states: list[str]
    rows: list[CampaignHistoryRow]


class SourceSegmentInput(ContractModel):
    id: str
    keyword: str
    state: str
    niche: str = ""
    niche_confirmed: bool = False
    status: Literal["active", "reduced", "paused"]
    cadence_multiplier: float
    seed_segment_id: str | None = None


class SourceSegment(ContractModel):
    id: str
    keyword: str
    state: str
    niche: str = ""
    niche_confirmed: bool = Field(alias="nicheConfirmed")
    status: Literal["active", "reduced", "paused"]
    cadence_multiplier: float = Field(alias="cadenceMultiplier")
    seed_segment_id: str | None = Field(default=None, alias="seedSegmentId")


class SourceSegments(ContractModel):
    version: int = Field(gt=0)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    segments: list[SourceSegment]
    scheduled: bool | None = None


class ActivateSourceSegments(ContractModel):
    version: int = Field(gt=0)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    segments: list[SourceSegmentInput]


class DatasetPublication(ContractModel):
    status: Literal["committed", "empty"]
    committed_at: datetime | None = Field(alias="committedAt")
    publication_date: date | None = Field(alias="publicationDate")
    business_count: int = Field(ge=0, alias="businessCount")
    lead_count: int = Field(ge=0, alias="leadCount")
    latest_job_at: datetime | None = Field(alias="latestJobAt")
    checksum: str | None = None


class NicheProposalSegment(ContractModel):
    id: str
    keyword: str
    state: str


class NicheProposalRequest(ContractModel):
    segments: list[NicheProposalSegment] = Field(min_length=1, max_length=500)


class NicheProposal(ContractModel):
    id: str
    niche: str


class NicheProposalResponse(ContractModel):
    proposals: list[NicheProposal]
    applied: Literal[False] = False


class AdjacentKeywordRequest(ContractModel):
    seed_keyword: str = Field(min_length=1, max_length=60)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=2000)
    count: int = Field(default=3, ge=1, le=10)


class AdjacentKeywordResponse(ContractModel):
    seed_keyword: str = Field(alias="seedKeyword")
    keywords: list[str]
    applied: Literal[False] = False


def keyword_version(keywords: list[str]) -> str:
    canonical = "\n".join(keywords) + ("\n" if keywords else "")
    return hashlib.sha256(canonical.encode()).hexdigest()


def keyword_diff(current: list[str], proposed: list[str]) -> KeywordDiff:
    current_keys = {item.casefold() for item in current}
    proposed_keys = {item.casefold() for item in proposed}
    return KeywordDiff(
        proposed=proposed,
        added=[item for item in proposed if item.casefold() not in current_keys],
        removed=[item for item in current if item.casefold() not in proposed_keys],
        unchanged=[item for item in proposed if item.casefold() in current_keys],
        expected_version=keyword_version(current),
    )


def runtime_version(configuration: RuntimeConfiguration) -> str:
    canonical = json.dumps(
        configuration.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def runtime_effects(
    current: RuntimeConfiguration,
    proposed: RuntimeConfiguration,
    current_cells: list[StateCellEffect],
    proposed_cells: list[StateCellEffect],
) -> RuntimeEffects:
    current_total = sum(item.cells for item in current_cells)
    proposed_total = sum(item.cells for item in proposed_cells)
    return RuntimeEffects(
        cells=proposed_cells,
        current_total_cells=current_total,
        proposed_total_cells=proposed_total,
        total_cell_delta=proposed_total - current_total,
        states_added=sorted(set(proposed.states) - set(current.states)),
        states_removed=sorted(set(current.states) - set(proposed.states)),
        runtime_changes=[
            name
            for name in RuntimeSettings.model_fields
            if getattr(current.settings, name) != getattr(proposed.settings, name)
        ],
        queue_changes=[
            name
            for name in QueueSettings.model_fields
            if getattr(current.queue, name) != getattr(proposed.queue, name)
        ],
        override_changes=sorted(
            state
            for state in set(current.overrides) | set(proposed.overrides)
            if current.overrides.get(state) != proposed.overrides.get(state)
        ),
    )
