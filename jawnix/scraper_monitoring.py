"""The native Scraper Operations monitoring contract (#63).

The GMS/OPS overview is nine regions that each refresh on their own cadence —
the pipeline log every two seconds, trends every sixty. That independence is
not decoration: it is what lets one region go dark while the other eight keep
showing real data. A single aggregate read would collapse all nine into one
failure, so this module keeps them separately addressable end to end.

Every field mirrors the upstream dashboard's own context, because this is a
parity rebuild: a value the operator can see today and cannot see here is a
regression. What Jawnix adds is the envelope — which region answered, when,
and whether what you are looking at is current — so a stale panel can say so
instead of quietly lying.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


#: Region key -> upstream refresh cadence, in seconds. These are the intervals
#: the rendered dashboard polls at; matching them keeps the operator's sense of
#: how live each number is.
REGION_INTERVALS: dict[str, int] = {
    "log": 2,
    "activity": 3,
    "stats": 10,
    "overall": 15,
    "stack": 15,
    "workers": 15,
    "top-states": 30,
    "trends": 60,
    "incidents": 60,
}

REGIONS = tuple(REGION_INTERVALS)

RegionKey = Literal[
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

#: How long a region's data may go unrefreshed before the screen must say so.
#: Three missed cycles: long enough to ride out one slow upstream response,
#: short enough that nobody reads a minute-old queue depth as current.
STALE_AFTER_CYCLES = 3


class StackStatus(BaseModel):
    """The single overall verdict, and everything that fed it."""

    key: Literal["operational", "idle", "attention", "stale"]
    label: str
    detail: str
    reasons: list[str] = Field(default_factory=list)
    age_seconds: int | None = None


class ServiceRow(BaseModel):
    key: str
    label: str
    state: Literal["ok", "neutral", "bad"]
    detail: str


class StackSample(BaseModel):
    """Latest host telemetry.

    ``services`` is deliberately absent: upstream carries the raw systemd blob
    there, and it is already projected into ServiceRow. Forwarding it too would
    publish Scraper host internals the screen never reads.
    """

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


class PipelineState(BaseModel):
    key: Literal["pausing", "paused", "running", "stopped"]
    label: str
    detail: str


class PauseInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: Upstream sends ``null`` when the pipeline is not paused, and the field is
    #: *present*, so a plain default never applies — pydantic rejects the None
    #: before reaching it. The fixture used "" for the same state, so this only
    #: failed against the real dashboard, and it failed the whole screen: the
    #: activity region is part of the initial load, so one null took out all
    #: nine regions behind the shell's error boundary.
    mode: str = ""
    cancelled_jobs: int = 0

    @field_validator("mode", mode="before")
    @classmethod
    def _null_mode_means_not_paused(cls, value: object) -> object:
        return "" if value is None else value

    @field_validator("cancelled_jobs", mode="before")
    @classmethod
    def _null_count_means_none_cancelled(cls, value: object) -> object:
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


class RegionData(BaseModel):
    """The union of every region's payload.

    One model rather than nine so a client holds a single region type and
    switches on the key. Only the fields belonging to the region that answered
    are populated; the rest stay None.
    """

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


class MonitoringRegion(BaseModel):
    """One region and the truth about how current it is.

    ``data`` is None only when the region has never been read successfully.
    A region that answered once and then failed keeps its last good payload
    and reports ``unavailable``, because the last known queue depth is more
    use to an operator than an empty panel.
    """

    region: RegionKey
    state: Literal["ok", "unavailable"]
    refresh_seconds: int
    fetched_at: datetime | None = None
    data: RegionData | None = None


class MonitoringSnapshot(BaseModel):
    """Every region, for a first paint."""

    service_state: Literal["connected", "unavailable"]
    last_successful_at: datetime | None = None
    idle_expires_in: int
    regions: list[MonitoringRegion]


#: Which upstream context keys belong to which region. The upstream projection
#: returns exactly these, so an unexpected key is a contract drift worth
#: dropping rather than forwarding blindly.
REGION_FIELDS: dict[str, tuple[str, ...]] = {
    "overall": ("stack_status",),
    "stack": ("sample", "stack_status", "services"),
    "stats": ("stats",),
    "activity": ("activity", "pipeline_state", "pause_info"),
    "log": ("pipeline_events",),
    "workers": ("workers", "expected_workers"),
    "trends": ("trends",),
    "incidents": ("incidents",),
    "top-states": ("top_states",),
}


def region_data(region: str, payload: dict) -> RegionData:
    """Normalize one upstream region payload into the Jawnix contract.

    Validation happens here rather than at the transport edge so a malformed
    upstream region fails as that region, leaving the other eight intact.
    """

    fields = REGION_FIELDS.get(region, ())
    return RegionData.model_validate(
        {key: payload[key] for key in fields if key in payload}
    )


def snapshot_regions(payload: dict, *, fetched_at: datetime) -> list[MonitoringRegion]:
    """Split one aggregate upstream read into its nine independent regions."""

    return [
        MonitoringRegion(
            region=region,
            state="ok",
            refresh_seconds=REGION_INTERVALS[region],
            fetched_at=fetched_at,
            data=region_data(region, payload),
        )
        for region in REGIONS
    ]
