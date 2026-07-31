"""Typed contracts for Scraper database browsing and exports."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


DATABASE_PAGE_SIZE = 50
STORED_EXPORT_NAME = re.compile(
    r"^[A-Z0-9_-]+(?:_[0-9-]+_[0-9]+)?\.csv$"
)
GENERATED_EXPORT_NAME = re.compile(
    r"^(?:"
    r"[A-Z]{2}-(?:all|uncategorized|[0-9]+-niches|"
    r"[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"|[A-Z]{2}(?:-[A-Z]{2}){0,3}"
    r"|[0-9]+-states"
    r")-phone-leads-[0-9]{4}-[0-9]{2}-[0-9]{2}\.csv$"
)


class ScraperDatabaseModel(BaseModel):
    """A strict model received from the private Scraper control service."""

    model_config = ConfigDict(extra="forbid")


class DatabaseTotals(ScraperDatabaseModel):
    businesses: int = Field(ge=0)
    unique_phones: int = Field(ge=0)


class DatabaseStateSummary(DatabaseTotals):
    state: str = Field(pattern=r"^[A-Z]{2}$")
    niches: int = Field(ge=0)


def _display_datetime(value: object) -> object:
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


class DatabaseBusiness(ScraperDatabaseModel):
    title: str
    phone: str | None = None
    website: str | None = None
    state: str | None = None
    niche: str | None = None
    last_seen: str

    @field_validator("title", mode="before")
    @classmethod
    def display_title(cls, value: object) -> object:
        return value or "Untitled"

    @field_validator("website", mode="before")
    @classmethod
    def safe_website(cls, value: object) -> object:
        if not isinstance(value, str) or not value:
            return None
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else None

    @field_validator("last_seen", mode="before")
    @classmethod
    def format_last_seen(cls, value: object) -> object:
        return _display_datetime(value)


class DatabaseBrowsePage(ScraperDatabaseModel):
    records: list[DatabaseBusiness]
    search: str
    state: str
    page: int = Field(ge=1)
    page_size: int = Field(default=DATABASE_PAGE_SIZE, ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
    has_previous: bool
    has_next: bool


class StoredExport(ScraperDatabaseModel):
    filename: str
    size_label: str


class ScraperDatabaseWorkspace(ScraperDatabaseModel):
    totals: DatabaseTotals
    states: list[DatabaseStateSummary]
    browse: DatabaseBrowsePage
    stored_exports: list[StoredExport]


class DatabaseWorkspace(ScraperDatabaseModel):
    service_state: Literal["connected", "unavailable"]
    last_successful_at: str | None = None
    idle_expires_in: int = Field(gt=0)
    totals: DatabaseTotals | None = None
    states: list[DatabaseStateSummary] = Field(default_factory=list)
    browse: DatabaseBrowsePage | None = None
    stored_exports: list[StoredExport] = Field(default_factory=list)


class DatabaseNiche(DatabaseTotals):
    key: str
    label: str


class ScraperDatabaseStateDetail(ScraperDatabaseModel):
    state: str = Field(pattern=r"^[A-Z]{2}$")
    totals: DatabaseStateSummary
    niches: list[DatabaseNiche]


class DatabaseStateDetail(ScraperDatabaseModel):
    service_state: Literal["connected", "unavailable"]
    last_successful_at: str | None = None
    idle_expires_in: int = Field(gt=0)
    state: str
    totals: DatabaseStateSummary | None = None
    niches: list[DatabaseNiche] = Field(default_factory=list)


class StateExportRequest(ScraperDatabaseModel):
    niches: list[str] | None = None


class MultiStateExportRequest(ScraperDatabaseModel):
    states: list[str] = Field(min_length=1, max_length=50)


class DatabaseExport(ScraperDatabaseModel):
    filename: str
    media_type: Literal["text/csv"] = "text/csv"
    content: str


class ExportRegeneration(ScraperDatabaseModel):
    generated: str
    stored_exports: list[StoredExport]
