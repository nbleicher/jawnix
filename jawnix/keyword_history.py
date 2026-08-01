"""Normalize and persist Jawnix-owned keyword observations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import KeywordHistory


KEYWORD_HISTORY_ORIGINS = frozenset(
    {
        "legacy_enqueue_log",
        "legacy_keyword_history",
        "legacy_businesses",
        "active_list",
        "winner",
        "accepted_save",
    }
)


@dataclass(frozen=True)
class KeywordObservation:
    term: str
    origin: str
    first_seen_at: datetime
    last_seen_at: datetime


def normalize_keyword_term(value: object) -> str:
    """Return the canonical case- and whitespace-insensitive keyword term."""

    return " ".join(str(value or "").split()).casefold()


def as_utc_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    else:
        raw = str(value or "").strip()
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                f"Invalid {field} timestamp: {raw or '<blank>'}"
            ) from error
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def upsert_keyword_history(
    session: Session,
    observations: Iterable[KeywordObservation],
) -> tuple[int, int]:
    """Merge observations into their normalized term-and-origin histories."""

    merged: dict[tuple[str, str], KeywordObservation] = {}
    for observation in observations:
        term = normalize_keyword_term(observation.term)
        if not term:
            continue
        if len(term) > 320:
            raise ValueError("Normalized keyword term exceeds 320 characters")
        if observation.origin not in KEYWORD_HISTORY_ORIGINS:
            raise ValueError(
                f"Unknown keyword history origin: {observation.origin}"
            )
        first_seen = as_utc_datetime(
            observation.first_seen_at,
            field="first-seen",
        )
        last_seen = as_utc_datetime(
            observation.last_seen_at,
            field="last-seen",
        )
        if first_seen > last_seen:
            raise ValueError("Keyword first-seen timestamp is after last-seen")
        key = (term, observation.origin)
        current = merged.get(key)
        merged[key] = KeywordObservation(
            term=term,
            origin=observation.origin,
            first_seen_at=(
                min(first_seen, current.first_seen_at)
                if current
                else first_seen
            ),
            last_seen_at=(
                max(last_seen, current.last_seen_at)
                if current
                else last_seen
            ),
        )

    if not merged:
        return 0, 0
    terms = {term for term, _origin in merged}
    origins = {origin for _term, origin in merged}
    existing = {
        (record.term, record.origin): record
        for record in session.scalars(
            select(KeywordHistory).where(
                KeywordHistory.term.in_(terms),
                KeywordHistory.origin.in_(origins),
            )
        )
    }
    inserted = len(set(merged) - set(existing))
    updated = sum(
        1
        for key, observation in merged.items()
        if key in existing
        and (
            observation.first_seen_at
            < as_utc_datetime(
                existing[key].first_seen_at,
                field="first-seen",
            )
            or observation.last_seen_at
            > as_utc_datetime(
                existing[key].last_seen_at,
                field="last-seen",
            )
        )
    )
    values = [
        {
            "term": observation.term,
            "origin": observation.origin,
            "first_seen_at": observation.first_seen_at,
            "last_seen_at": observation.last_seen_at,
        }
        for observation in merged.values()
    ]
    dialect = session.get_bind().dialect.name
    for offset in range(0, len(values), 500):
        batch = values[offset : offset + 500]
        if dialect == "postgresql":
            statement = postgresql_insert(KeywordHistory).values(batch)
            statement = statement.on_conflict_do_update(
                index_elements=[
                    KeywordHistory.term,
                    KeywordHistory.origin,
                ],
                set_={
                    "first_seen_at": func.least(
                        KeywordHistory.first_seen_at,
                        statement.excluded.first_seen_at,
                    ),
                    "last_seen_at": func.greatest(
                        KeywordHistory.last_seen_at,
                        statement.excluded.last_seen_at,
                    ),
                },
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(KeywordHistory).values(batch)
            statement = statement.on_conflict_do_update(
                index_elements=[
                    KeywordHistory.term,
                    KeywordHistory.origin,
                ],
                set_={
                    "first_seen_at": func.min(
                        KeywordHistory.first_seen_at,
                        statement.excluded.first_seen_at,
                    ),
                    "last_seen_at": func.max(
                        KeywordHistory.last_seen_at,
                        statement.excluded.last_seen_at,
                    ),
                },
            )
        else:
            for value in batch:
                key = (value["term"], value["origin"])
                record = existing.get(key)
                if record is None:
                    session.add(KeywordHistory(**value))
                else:
                    record.first_seen_at = min(
                        as_utc_datetime(
                            record.first_seen_at,
                            field="first-seen",
                        ),
                        value["first_seen_at"],
                    )
                    record.last_seen_at = max(
                        as_utc_datetime(
                            record.last_seen_at,
                            field="last-seen",
                        ),
                        value["last_seen_at"],
                    )
            continue
        session.execute(statement)
    session.flush()
    for record in existing.values():
        session.expire(record)
    return inserted, updated


def observe_keyword_history(
    session: Session,
    terms: Iterable[str],
    *,
    origin: str,
    observed_at: datetime | None = None,
) -> tuple[int, int]:
    observed_at = observed_at or datetime.now(timezone.utc)
    return upsert_keyword_history(
        session,
        (
            KeywordObservation(
                term=term,
                origin=origin,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
            )
            for term in terms
        ),
    )
