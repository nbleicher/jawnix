"""Typed contracts for Scraper state and grid coverage."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


STATE_CARDS_REFRESH_SECONDS = 20
STATE_KEYWORDS_REFRESH_SECONDS = 10
STATE_CELLS_REFRESH_SECONDS = 15

CoverageStatus = Literal["covered", "partial", "uncovered"]
CellStatus = Literal["posted", "reserved", "failed", "uncovered"]
FeedState = Literal["ok", "unavailable"]


class ScraperCoverageModel(BaseModel):
    """A strict model received from the private Scraper control service."""

    model_config = ConfigDict(extra="forbid")


class StateCoverageCard(ScraperCoverageModel):
    state: str
    businesses: int = Field(ge=0)
    posted_cells: int = Field(ge=0)
    total_cells: int = Field(ge=0)
    active_keywords: int = Field(ge=0)
    coverage: int = Field(ge=0, le=100)
    status: CoverageStatus


class StateKeywordActivity(ScraperCoverageModel):
    keyword: str
    businesses: int = Field(ge=0)
    posted_cells: int = Field(ge=0)
    total_cells: int = Field(ge=0)
    coverage: int = Field(ge=0, le=100)
    empty_rate: float = Field(ge=0)
    last_enqueued: str | None = None

    @field_validator("last_enqueued", mode="before")
    @classmethod
    def format_last_enqueued(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.strftime("%b %d, %H:%M")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                ).strftime("%b %d, %H:%M")
            except ValueError:
                return value
        return value


class StateGridCell(ScraperCoverageModel):
    index: int = Field(ge=1)
    cell: str
    status: CellStatus


class StateGridCoverage(ScraperCoverageModel):
    cells: list[StateGridCell]
    posted: int = Field(ge=0)
    reserved: int = Field(ge=0)
    failed: int = Field(ge=0)
    uncovered: int = Field(ge=0)


class CoverageStates(ScraperCoverageModel):
    states: list[StateCoverageCard]


class StateKeywords(ScraperCoverageModel):
    state: str
    keywords: list[StateKeywordActivity]


class ScraperStateCoverageDetail(ScraperCoverageModel):
    state: str
    keywords: list[StateKeywordActivity]
    cells: StateGridCoverage


DataT = TypeVar("DataT")


class CoverageFeed(BaseModel, Generic[DataT]):
    state: FeedState
    refresh_seconds: int
    fetched_at: datetime | None = None
    data: DataT | None = None


class StateCoverageSnapshot(BaseModel):
    service_state: Literal["connected", "unavailable"]
    last_successful_at: datetime | None = None
    idle_expires_in: int
    states: CoverageFeed[list[StateCoverageCard]]


class StateCoverageDetail(BaseModel):
    state: str
    service_state: Literal["connected", "degraded", "unavailable"]
    last_successful_at: datetime | None = None
    idle_expires_in: int
    keywords: CoverageFeed[list[StateKeywordActivity]]
    cells: CoverageFeed[StateGridCoverage]
