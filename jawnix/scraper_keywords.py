"""Typed keyword-management models and line-list semantics."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_KEYWORD_TEXT_LENGTH = 1_000_000


class KeywordRollover(BaseModel):
    enabled: bool
    state: Literal["off", "working", "draining", "ready"]
    label: str
    detail: str
    percent_complete: int = Field(ge=0, le=100)
    posted_jobs: int | None = None
    expected_jobs: int | None = None
    last_status: Literal["generated", "error"] | None = None
    last_event: str | None = None

    @field_validator("last_event", mode="before")
    @classmethod
    def format_last_event(cls, value: object) -> object:
        if isinstance(value, datetime):
            moment = value
        elif isinstance(value, str):
            try:
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        else:
            return value
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc)
        return moment.strftime("%b %d · %H:%M UTC")


class KeywordWinner(BaseModel):
    rank: int = Field(ge=1)
    keyword: str
    phone_businesses: int = Field(ge=0)
    businesses: int = Field(ge=0)
    posted_cells: int = Field(ge=0)
    phones_per_cell: float = Field(ge=0)
    phone_rate: float = Field(ge=0)
    last_used: str

    @field_validator("last_used", mode="before")
    @classmethod
    def format_last_used(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.strftime("%b %d")
        if isinstance(value, date):
            return value.strftime("%b %d")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                ).strftime("%b %d")
            except ValueError:
                return value
        return value


class ScraperKeywordWorkspace(BaseModel):
    """The private Scraper control service's keyword workspace."""

    model_config = ConfigDict(extra="forbid")

    current: list[str]
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    ai_enabled: bool
    rollover: KeywordRollover
    winners: list[KeywordWinner]


class ScraperKeywordWinners(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winners: list[KeywordWinner]


class KeywordWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_state: Literal["connected", "unavailable"]
    last_successful_at: str | None = None
    current: list[str]
    version: str
    ai_enabled: bool
    rollover: KeywordRollover
    winners: list[KeywordWinner]
    idle_expires_in: int = Field(gt=0)


class KeywordTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_KEYWORD_TEXT_LENGTH)


class KeywordSaveRequest(KeywordTextRequest):
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_token: str = Field(min_length=1, max_length=2000)
    enqueue: bool = False
    generation_id: str | None = None

    @field_validator("generation_id")
    @classmethod
    def generation_id_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return value


class KeywordRolloverEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["generated", "error"]
    previous_keywords: list[str] | None = None
    next_keywords: list[str] | None = None
    message: str = Field(max_length=300)


class KeywordGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["broad", "adjacent"] = "broad"
    seed_keyword: str | None = Field(default=None, max_length=200)

    @field_validator("seed_keyword")
    @classmethod
    def normalize_seed(cls, value: str | None) -> str | None:
        value = value.strip() if value else ""
        return value or None


class KeywordRolloverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["enable", "disable"]


class KeywordDiff(BaseModel):
    proposed: list[str]
    added: list[str]
    removed: list[str]
    unchanged: list[str]
    expected_version: str
    review_token: str = ""


class KeywordSaveResult(BaseModel):
    saved: bool = True
    enqueued: bool
    current: list[str]
    version: str
    diff: KeywordDiff


class KeywordGenerationDraft(BaseModel):
    generation_id: str
    mode: Literal["broad", "adjacent"]
    seed_keyword: str | None = None
    keywords: list[str]
    excluded_count: int = Field(ge=0)
    notice: str


def parse_keyword_text(text: str) -> list[str]:
    """Apply the Scraper's supported text-file format exactly.

    Blank lines and ``#`` comments are ignored. Whitespace is trimmed and the
    first spelling of a case-insensitive duplicate wins.
    """

    keywords: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        keyword = line.strip()
        key = keyword.casefold()
        if keyword and not keyword.startswith("#") and key not in seen:
            seen.add(key)
            keywords.append(keyword)
    return keywords


def keyword_version(keywords: list[str]) -> str:
    canonical = "\n".join(keywords) + ("\n" if keywords else "")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_keywords(current: list[str], text: str) -> KeywordDiff:
    proposed = parse_keyword_text(text)
    current_keys = {item.casefold() for item in current}
    proposed_keys = {item.casefold() for item in proposed}
    return KeywordDiff(
        proposed=proposed,
        added=[
            item for item in proposed if item.casefold() not in current_keys
        ],
        removed=[
            item for item in current if item.casefold() not in proposed_keys
        ],
        unchanged=[
            item for item in proposed if item.casefold() in current_keys
        ],
        expected_version=keyword_version(current),
    )
