from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from jawnix.models import KeywordHistory
from jawnix_data.cli import app
from jawnix_data.keyword_history import import_keyword_history


def scraper_history_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE enqueue_log (
                keyword TEXT NOT NULL,
                enqueued_at TEXT NOT NULL
            );
            CREATE TABLE keyword_history (
                keyword TEXT NOT NULL,
                last_enqueued TEXT NOT NULL
            );
            CREATE TABLE businesses (
                keyword TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO enqueue_log VALUES (?, ?)",
            [
                ("  Roof   Repair  ", "2026-07-01T12:00:00+00:00"),
                ("ROOF REPAIR", "2026-07-03T12:00:00+00:00"),
                ("Plumbers", "2026-07-02T12:00:00+00:00"),
            ],
        )
        connection.executemany(
            "INSERT INTO keyword_history VALUES (?, ?)",
            [
                (" roof repair ", "2026-06-15"),
                ("Electricians", "2026-06-20"),
            ],
        )
        connection.executemany(
            "INSERT INTO businesses VALUES (?, ?, ?)",
            [
                (
                    "ROOF   REPAIR",
                    "2026-05-01T08:00:00Z",
                    "2026-07-04T08:00:00Z",
                ),
                (
                    "   ",
                    "2026-05-01T08:00:00Z",
                    "2026-07-04T08:00:00Z",
                ),
            ],
        )


def test_import_normalizes_the_legacy_union_and_reports_source_proof(
    session,
    tmp_path,
):
    source = tmp_path / "scraper.db"
    scraper_history_database(source)
    expected_checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    result = import_keyword_history(
        session,
        source,
        expected_checksum=expected_checksum,
    )
    session.commit()

    assert result == {
        "skipped": False,
        "sourceRows": 7,
        "sourceRowsByTable": {
            "enqueue_log": 3,
            "keyword_history": 2,
            "businesses": 2,
        },
        "candidateRows": 6,
        "unionTerms": 3,
        "records": 5,
        "imported": 5,
        "inserted": 5,
        "updated": 0,
        "checksum": expected_checksum,
    }
    records = list(
        session.scalars(
            select(KeywordHistory).order_by(
                KeywordHistory.term,
                KeywordHistory.origin,
            )
        )
    )
    assert [(record.term, record.origin) for record in records] == [
        ("electricians", "legacy_keyword_history"),
        ("plumbers", "legacy_enqueue_log"),
        ("roof repair", "legacy_businesses"),
        ("roof repair", "legacy_enqueue_log"),
        ("roof repair", "legacy_keyword_history"),
    ]
    enqueue = next(
        record
        for record in records
        if record.term == "roof repair"
        and record.origin == "legacy_enqueue_log"
    )
    assert enqueue.first_seen_at.replace(tzinfo=timezone.utc) == datetime(
        2026,
        7,
        1,
        12,
        tzinfo=timezone.utc,
    )
    assert enqueue.last_seen_at.replace(tzinfo=timezone.utc) == datetime(
        2026,
        7,
        3,
        12,
        tzinfo=timezone.utc,
    )


def test_import_is_idempotent_by_checksum_and_refuses_a_mismatch(
    session,
    tmp_path,
):
    source = tmp_path / "scraper.db"
    scraper_history_database(source)

    first = import_keyword_history(session, source)
    session.commit()
    second = import_keyword_history(session, source)

    assert second == {**first, "skipped": True, "inserted": 0, "updated": 0}
    assert len(session.scalars(select(KeywordHistory)).all()) == 5


def test_a_new_snapshot_extends_existing_seen_ranges(session, tmp_path):
    first_source = tmp_path / "first.db"
    scraper_history_database(first_source)
    import_keyword_history(session, first_source)
    session.commit()

    later_source = tmp_path / "later.db"
    scraper_history_database(later_source)
    with sqlite3.connect(later_source) as connection:
        connection.execute(
            "INSERT INTO enqueue_log VALUES (?, ?)",
            (" PLUMBERS ", "2026-07-10T12:00:00+00:00"),
        )

    result = import_keyword_history(session, later_source)
    session.commit()

    assert result["inserted"] == 0
    assert result["updated"] == 1
    plumbers = session.scalars(
        select(KeywordHistory).where(
            KeywordHistory.term == "plumbers",
            KeywordHistory.origin == "legacy_enqueue_log",
        )
    ).one()
    assert plumbers.first_seen_at.replace(tzinfo=timezone.utc) == datetime(
        2026,
        7,
        2,
        12,
        tzinfo=timezone.utc,
    )
    assert plumbers.last_seen_at.replace(tzinfo=timezone.utc) == datetime(
        2026,
        7,
        10,
        12,
        tzinfo=timezone.utc,
    )


def test_import_command_reports_the_verification_checksum(
    session,
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "scraper.db"
    scraper_history_database(source)
    expected_checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr("jawnix_data.cli.SessionLocal", factory)

    result = CliRunner().invoke(
        app,
        [
            "import-keyword-history",
            str(source),
            "--expected-sha256",
            expected_checksum,
        ],
    )

    assert result.exit_code == 0
    assert f'"checksum": "{expected_checksum}"' in result.stdout
    assert '"sourceRows": 7' in result.stdout

    with pytest.raises(ValueError, match="Checksum mismatch"):
        import_keyword_history(
            session,
            source,
            expected_checksum="0" * 64,
        )
    assert len(session.scalars(select(KeywordHistory)).all()) == 5
