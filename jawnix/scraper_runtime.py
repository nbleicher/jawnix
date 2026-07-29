"""Typed projections for Scale campaign history and runtime configuration."""

from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
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


def runtime_form(configuration: RuntimeConfiguration) -> dict[str, object]:
    form: dict[str, object] = {
        "states": [state.lower() for state in configuration.states],
        "zoom": str(configuration.settings.zoom),
        "radius": str(configuration.settings.radius),
        "depth": str(configuration.settings.depth),
        "lang": configuration.settings.lang,
        "timeout": str(configuration.settings.timeout),
        "target_depth": str(configuration.queue.target_depth),
        "target_per_worker": str(configuration.queue.target_per_worker),
        "min_target_depth": str(configuration.queue.min_target_depth),
        "max_target_depth": str(configuration.queue.max_target_depth),
        "batch_size": str(configuration.queue.batch_size),
        "poll_secs": str(configuration.queue.poll_secs),
        "skip_recent_days": str(configuration.queue.skip_recent_days),
    }
    if configuration.settings.fast_mode:
        form["fast_mode"] = "on"
    for state, override in configuration.overrides.items():
        code = state.lower()
        if override.cell_size_km is not None:
            form[f"cell_size_km_{code}"] = str(override.cell_size_km)
        if override.zoom is not None:
            form[f"zoom_{code}"] = str(override.zoom)
    return form


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


class _ScaleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: list[dict[str, str | None]] = []
        self.rows: list[list[str]] = []
        self.cell_effects: list[StateCellEffect] = []
        self.saw_tbody = False
        self._in_tbody = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._state_text: list[str] | None = None
        self._pending_state: str | None = None
        self._strong_text: list[str] | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "input":
            self.inputs.append(attributes)
        elif tag == "tbody":
            self._in_tbody = True
            self.saw_tbody = True
        elif tag == "tr" and self._in_tbody:
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []
        elif tag == "span" and "state-code" in (
            attributes.get("class") or ""
        ).split():
            self._state_text = []
        elif tag == "strong" and self._pending_state is not None:
            self._strong_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "tbody":
            self._in_tbody = False
        elif tag == "td" and self._cell is not None:
            if self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "span" and self._state_text is not None:
            state = "".join(self._state_text).strip().upper()
            self._pending_state = state if state in US_STATES else None
            self._state_text = None
        elif tag == "strong" and self._strong_text is not None:
            value = "".join(self._strong_text).replace(",", "").strip()
            if self._pending_state and value.isdigit():
                self.cell_effects.append(
                    StateCellEffect(
                        state=self._pending_state,
                        cells=int(value),
                    )
                )
            self._pending_state = None
            self._strong_text = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._state_text is not None:
            self._state_text.append(data)
        if self._strong_text is not None:
            self._strong_text.append(data)


def _number(
    inputs: dict[str, dict[str, str | None]],
    name: str,
    default: int | float,
    *,
    integer: bool = False,
) -> int | float:
    raw = inputs.get(name, {}).get("value")
    if raw in (None, ""):
        return int(default) if integer else float(default)
    return int(float(raw)) if integer else float(raw)


def parse_runtime_workspace(
    html: str,
) -> tuple[RuntimeConfiguration, list[str], list[StateCellEffect]]:
    parser = _ScaleHTML()
    parser.feed(html)
    named = [
        item for item in parser.inputs if isinstance(item.get("name"), str)
    ]
    by_name = {str(item["name"]): item for item in named}
    state_inputs = [item for item in named if item.get("name") == "states"]
    all_states = sorted(
        {
            str(item.get("value") or "").upper()
            for item in state_inputs
            if str(item.get("value") or "").upper() in US_STATES
        }
    )
    required_inputs = {
        "zoom",
        "radius",
        "depth",
        "lang",
        "fast_mode",
        "timeout",
        "target_depth",
        "target_per_worker",
        "min_target_depth",
        "max_target_depth",
        "batch_size",
        "poll_secs",
        "skip_recent_days",
    }
    if set(all_states) != set(US_STATES) or not required_inputs.issubset(
        by_name
    ):
        raise ValueError("Invalid runtime configuration document.")
    states = [
        str(item.get("value") or "").upper()
        for item in state_inputs
        if "checked" in item
    ]
    overrides: dict[str, StateOverride] = {}
    for state in states:
        code = state.lower()
        cell_raw = by_name.get(f"cell_size_km_{code}", {}).get("value")
        zoom_raw = by_name.get(f"zoom_{code}", {}).get("value")
        values: dict[str, int | float] = {}
        if cell_raw not in (None, ""):
            values["cell_size_km"] = float(cell_raw)
        if zoom_raw not in (None, ""):
            values["zoom"] = int(float(zoom_raw))
        if values:
            overrides[state] = StateOverride(**values)
    configuration = RuntimeConfiguration(
        states=states,
        settings=RuntimeSettings(
            zoom=_number(by_name, "zoom", 15, integer=True),
            radius=_number(by_name, "radius", 10_000),
            depth=_number(by_name, "depth", 3, integer=True),
            lang=str(by_name.get("lang", {}).get("value") or "en"),
            fast_mode="checked" in by_name.get("fast_mode", {}),
            timeout=_number(by_name, "timeout", 300, integer=True),
        ),
        queue=QueueSettings(
            target_depth=_number(
                by_name,
                "target_depth",
                50,
                integer=True,
            ),
            target_per_worker=_number(
                by_name,
                "target_per_worker",
                25,
                integer=True,
            ),
            min_target_depth=_number(
                by_name,
                "min_target_depth",
                25,
                integer=True,
            ),
            max_target_depth=_number(
                by_name,
                "max_target_depth",
                500,
                integer=True,
            ),
            batch_size=_number(
                by_name,
                "batch_size",
                100,
                integer=True,
            ),
            poll_secs=_number(
                by_name,
                "poll_secs",
                5,
                integer=True,
            ),
            skip_recent_days=_number(
                by_name,
                "skip_recent_days",
                0,
                integer=True,
            ),
        ),
        overrides=overrides,
    )
    expected_states = set(configuration.states)
    cell_states = [row.state for row in parser.cell_effects]
    if (
        len(cell_states) != len(expected_states)
        or set(cell_states) != expected_states
    ):
        raise ValueError("Invalid runtime configuration cell counts.")
    return configuration, all_states, parser.cell_effects


def parse_cell_effects(html: str) -> list[StateCellEffect]:
    parser = _ScaleHTML()
    parser.feed(html)
    return parser.cell_effects


def parse_campaign_history(html: str) -> list[CampaignHistoryRow]:
    parser = _ScaleHTML()
    parser.feed(html)
    if not parser.saw_tbody:
        raise ValueError("Invalid campaign history document.")
    rows: list[CampaignHistoryRow] = []
    for cells in parser.rows:
        if len(cells) != 6:
            raise ValueError("Invalid campaign history row.")
        try:
            cells_posted = int(cells[2].replace(",", "") or "0")
        except ValueError as exc:
            raise ValueError("Invalid campaign history row.") from exc
        state = cells[1].upper()
        if state not in US_STATES:
            raise ValueError("Invalid campaign history state.")
        rows.append(
            CampaignHistoryRow(
                keyword=cells[0],
                state=state,
                cells_posted=cells_posted,
                first_enqueued=None if cells[3] == "—" else cells[3],
                latest_enqueued=None if cells[4] == "—" else cells[4],
                campaign_date=cells[5],
            )
        )
    return rows
