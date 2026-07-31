"""Typed projections for Scale campaign history and runtime configuration."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .states import US_STATES


HistorySort = Literal[
    "keyword",
    "state",
    "last_enqueued",
    "cells_posted",
    "latest_enqueued",
]
SortDirection = Literal["asc", "desc"]


class CampaignHistoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str
    state: str
    cells_posted: int = 0
    first_enqueued: str | None = None
    latest_enqueued: str | None = None
    campaign_date: str


class CampaignHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_state: Literal["connected", "unavailable"]
    last_successful_at: str | None = None
    idle_expires_in: int
    search: str = ""
    state: str = ""
    sort: HistorySort = "last_enqueued"
    direction: SortDirection = "desc"
    all_states: list[str] = Field(default_factory=list)
    rows: list[CampaignHistoryRow] = Field(default_factory=list)


class ControlCampaignHistoryRow(BaseModel):
    """Campaign history as returned by the private control service."""

    model_config = ConfigDict(extra="forbid")

    keyword: str
    state: str
    cells_posted: int = Field(ge=0)
    first_enqueued: datetime | None = None
    latest_enqueued: datetime | None = None
    campaign_date: date


class ControlCampaignHistory(BaseModel):
    """Typed control-service response before Jawnix adds its envelope."""

    model_config = ConfigDict(extra="forbid")

    search: str = ""
    state: str = ""
    sort: HistorySort = "last_enqueued"
    direction: SortDirection = "desc"
    all_states: list[str]
    rows: list[ControlCampaignHistoryRow]


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class QueueSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
            raise ValueError(
                "Minimum queue depth cannot exceed maximum queue depth."
            )
        return self


class StateOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cell_size_km: float | None = Field(default=None, ge=1, le=500)
    zoom: int | None = Field(default=None, ge=1, le=21)

    @model_validator(mode="after")
    def has_value(self):
        if self.cell_size_km is None and self.zoom is None:
            raise ValueError("A state override must change at least one value.")
        return self


class RuntimeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
        invalid = sorted(set(states) - US_STATES)
        if invalid:
            raise ValueError(f"Unknown states: {', '.join(invalid)}")
        return sorted(states)

    @field_validator("overrides")
    @classmethod
    def normalize_override_keys(
        cls,
        values: dict[str, StateOverride],
    ) -> dict[str, StateOverride]:
        normalized: dict[str, StateOverride] = {}
        for raw_state, override in values.items():
            state = raw_state.strip().upper()
            if state not in US_STATES:
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
                "State overrides require an active state: "
                + ", ".join(inactive)
            )
        return self


class FieldBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum: float
    maximum: float
    step: float = 1


class RuntimeBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class StateCellEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    cells: int = Field(ge=0)


class RuntimeEffects(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cells: list[StateCellEffect]
    current_total_cells: int = Field(ge=0)
    proposed_total_cells: int = Field(ge=0)
    total_cell_delta: int
    states_added: list[str]
    states_removed: list[str]
    runtime_changes: list[str]
    queue_changes: list[str]
    override_changes: list[str]


class RuntimeWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_state: Literal["connected", "unavailable"]
    last_successful_at: str | None = None
    idle_expires_in: int
    current: RuntimeConfiguration
    version: str
    all_states: list[str]
    cells: list[StateCellEffect]
    total_cells: int = Field(ge=0)
    bounds: RuntimeBounds = RUNTIME_BOUNDS


class RuntimePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration: RuntimeConfiguration


class RuntimePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration: RuntimeConfiguration
    expected_version: str
    proposed_version: str
    review_token: str
    effects: RuntimeEffects


class RuntimeSaveRequest(RuntimePreviewRequest):
    expected_version: str = Field(min_length=64, max_length=64)
    review_token: str = Field(min_length=1)
    enqueue: bool = False
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Record why you are changing runtime configuration.")
        return normalized


class RuntimeSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    version: str
    configuration: RuntimeConfiguration
    effects: RuntimeEffects
    enqueued: bool


class ControlRuntimeWorkspace(BaseModel):
    """Runtime workspace returned by the private control service."""

    model_config = ConfigDict(extra="forbid")

    current: RuntimeConfiguration
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    all_states: list[str]
    cells: list[StateCellEffect]
    total_cells: int = Field(ge=0)
    bounds: RuntimeBounds = RUNTIME_BOUNDS


class ControlRuntimePreview(BaseModel):
    """Runtime preview before Jawnix issues its signed review receipt."""

    model_config = ConfigDict(extra="forbid")

    configuration: RuntimeConfiguration
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposed_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    effects: RuntimeEffects


class ControlRuntimeSaveRequest(RuntimePreviewRequest):
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    enqueue: bool = False


class ControlRuntimeSaveResult(BaseModel):
    """Runtime save result before Jawnix journals the local revision."""

    model_config = ConfigDict(extra="forbid")

    saved: bool = True
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: RuntimeConfiguration
    effects: RuntimeEffects
    enqueued: bool


def runtime_version(configuration: RuntimeConfiguration) -> str:
    canonical = json.dumps(
        configuration.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def runtime_summary(configuration: RuntimeConfiguration) -> dict[str, object]:
    """Return the safe, topology-free portion of Scale's mutable config."""

    return {
        "activeStates": configuration.states,
        "runtime": configuration.settings.model_dump(mode="json"),
        "queue": configuration.queue.model_dump(mode="json"),
        "stateOverrides": {
            state: override.model_dump(mode="json", exclude_none=True)
            for state, override in configuration.overrides.items()
        },
    }


def calculate_effects(
    current: RuntimeConfiguration,
    proposed: RuntimeConfiguration,
    current_cells: list[StateCellEffect],
    proposed_cells: list[StateCellEffect],
) -> RuntimeEffects:
    current_total = sum(row.cells for row in current_cells)
    proposed_total = sum(row.cells for row in proposed_cells)
    runtime_changes = [
        name
        for name in RuntimeSettings.model_fields
        if getattr(current.settings, name) != getattr(proposed.settings, name)
    ]
    queue_changes = [
        name
        for name in QueueSettings.model_fields
        if getattr(current.queue, name) != getattr(proposed.queue, name)
    ]
    override_changes = sorted(
        state
        for state in set(current.overrides) | set(proposed.overrides)
        if current.overrides.get(state) != proposed.overrides.get(state)
    )
    return RuntimeEffects(
        cells=proposed_cells,
        current_total_cells=current_total,
        proposed_total_cells=proposed_total,
        total_cell_delta=proposed_total - current_total,
        states_added=sorted(set(proposed.states) - set(current.states)),
        states_removed=sorted(set(current.states) - set(proposed.states)),
        runtime_changes=runtime_changes,
        queue_changes=queue_changes,
        override_changes=override_changes,
    )
