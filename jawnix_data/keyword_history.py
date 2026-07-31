"""Import legacy Scraper keyword history into Jawnix."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jawnix.keyword_history import (
    KeywordObservation,
    as_utc_datetime,
    normalize_keyword_term,
    upsert_keyword_history,
)
from jawnix.models import KeywordHistoryImport

from .migration import require_checksum


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    }


def _require_source_schema(connection: sqlite3.Connection) -> None:
    required = {
        "enqueue_log": {"keyword"},
        "keyword_history": {"keyword", "last_enqueued"},
        "businesses": {"keyword", "first_seen", "last_seen"},
    }
    problems = []
    for table, columns in required.items():
        actual = _table_columns(connection, table)
        missing = sorted(columns - actual)
        if table == "enqueue_log" and not {
            "enqueued_at",
            "day",
        }.intersection(actual):
            missing.append("enqueued_at or day")
        if missing:
            problems.append(f"{table}: {', '.join(missing)}")
    if problems:
        raise ValueError(
            "Scraper keyword-history source is missing required columns ("
            + "; ".join(problems)
            + ")"
        )


def _source_observations(
    connection: sqlite3.Connection,
) -> tuple[dict[str, int], list[KeywordObservation], int]:
    counts = {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in ("enqueue_log", "keyword_history", "businesses")
    }
    observations: dict[tuple[str, str], KeywordObservation] = {}
    candidate_rows = 0

    enqueue_columns = _table_columns(connection, "enqueue_log")
    if "enqueued_at" in enqueue_columns and "day" in enqueue_columns:
        enqueue_seen = "COALESCE(enqueued_at, day)"
    elif "enqueued_at" in enqueue_columns:
        enqueue_seen = "enqueued_at"
    else:
        enqueue_seen = "day"
    sources = (
        (
            "legacy_enqueue_log",
            f"SELECT keyword, {enqueue_seen} AS first_seen, "
            f"{enqueue_seen} AS last_seen FROM enqueue_log",
        ),
        (
            "legacy_keyword_history",
            "SELECT keyword, last_enqueued AS first_seen, "
            "last_enqueued AS last_seen FROM keyword_history",
        ),
        (
            "legacy_businesses",
            "SELECT keyword, first_seen, last_seen FROM businesses",
        ),
    )
    for origin, query in sources:
        for row in connection.execute(query):
            term = normalize_keyword_term(row["keyword"])
            if not term:
                continue
            candidate_rows += 1
            first_seen = as_utc_datetime(
                row["first_seen"],
                field=f"{origin} first-seen",
            )
            last_seen = as_utc_datetime(
                row["last_seen"],
                field=f"{origin} last-seen",
            )
            key = (term, origin)
            current = observations.get(key)
            observations[key] = KeywordObservation(
                term=term,
                origin=origin,
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
    return counts, list(observations.values()), candidate_rows


def import_keyword_history(
    session: Session,
    path: Path,
    expected_checksum: str | None = None,
) -> dict:
    """Import the normalized union from one immutable Scraper DB snapshot."""

    checksum = require_checksum(path, expected_checksum)
    existing = session.scalar(
        select(KeywordHistoryImport).where(
            KeywordHistoryImport.checksum == checksum
        )
    )
    if existing is not None:
        return {
            **existing.report,
            "skipped": True,
            "inserted": 0,
            "updated": 0,
        }

    connection = sqlite3.connect(
        f"file:{path}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        _require_source_schema(connection)
        source_rows, observations, candidate_rows = _source_observations(
            connection
        )
    finally:
        connection.close()

    inserted, updated = upsert_keyword_history(session, observations)
    report = {
        "skipped": False,
        "sourceRows": sum(source_rows.values()),
        "sourceRowsByTable": source_rows,
        "candidateRows": candidate_rows,
        "unionTerms": len({item.term for item in observations}),
        "records": len({(item.term, item.origin) for item in observations}),
        "imported": inserted,
        "inserted": inserted,
        "updated": updated,
        "checksum": checksum,
    }
    session.add(
        KeywordHistoryImport(
            source_path=str(path),
            checksum=checksum,
            report=report,
        )
    )
    session.flush()
    return report
