"""Batch numbers: sequential, unique, and immutable, backfilled in submission order."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from jawnix.models import Agent, CustomerProfile, LeadRequest

from conftest import make_request


def _migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261109_0054_batch_numbers.py"
    )
    spec = importlib.util.spec_from_file_location("batch_number_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _legacy_table(connection) -> None:
    connection.execute(
        text(
            "CREATE TABLE lead_requests (id CHAR(32) PRIMARY KEY, "
            "created_at DATETIME NOT NULL, lead_count INTEGER NOT NULL)"
        )
    )


def test_upgrade_numbers_existing_batches_in_submission_order(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with engine.begin() as connection:
        _legacy_table(connection)
        # Inserted out of order: numbering follows created_at, then id.
        for id_, offset in (("c", 2), ("a", 0), ("d", 2), ("b", 1)):
            connection.execute(
                text("INSERT INTO lead_requests VALUES (:id, :at, 10)"),
                {"id": id_ * 32, "at": base + timedelta(days=offset)},
            )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            # Guarded: a schema built from current models already has it.
            migration.upgrade()
        rows = connection.execute(
            text("SELECT substr(id, 1, 1), number FROM lead_requests ORDER BY number")
        ).all()
        assert rows == [("a", 1001), ("b", 1002), ("c", 1003), ("d", 1004)]
        column = next(
            item
            for item in inspect(connection).get_columns("lead_requests")
            if item["name"] == "number"
        )
        assert column["nullable"] is False
        with pytest.raises(IntegrityError):
            connection.execute(
                text("UPDATE lead_requests SET number = 1001 WHERE id = :id"),
                {"id": "b" * 32},
            )
    engine.dispose()


def test_upgrade_of_an_empty_table_and_downgrade(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        _legacy_table(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.downgrade()
        assert {
            item["name"] for item in inspect(connection).get_columns("lead_requests")
        } == {"id", "created_at", "lead_count"}
    engine.dispose()


def test_downgrade_refuses_once_numbers_left_submission_order(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        _legacy_table(connection)
        connection.execute(
            text(
                "INSERT INTO lead_requests VALUES "
                "(:a, '2026-07-01', 10), (:b, '2026-07-02', 10)"
            ),
            {"a": "a" * 32, "b": "b" * 32},
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        # A rolled-back submission leaves a gap a later backfill would close.
        connection.execute(
            text("UPDATE lead_requests SET number = 1003 WHERE id = :b"),
            {"b": "b" * 32},
        )
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="submission order"):
                migration.downgrade()
        assert "number" in {
            item["name"] for item in inspect(connection).get_columns("lead_requests")
        }
    engine.dispose()


def test_new_batches_continue_from_the_highest_number(session):
    customer = Agent(slug="northstar", name="Northstar Insurance")
    session.add(customer)
    session.flush()
    first = make_request(session, customer, 10)
    second = make_request(session, customer, 10)
    assert (first.number, second.number) == (1001, 1002)

    # Several Batch Requests flushed together still get distinct numbers.
    batch = []
    for _ in range(3):
        user_id = uuid.uuid4()
        session.add(
            CustomerProfile(
                user_id=user_id,
                email=f"{user_id}@example.com",
                agent=customer,
            )
        )
        batch.append(
            LeadRequest(
                user_id=user_id,
                agent=customer,
                lead_count=5,
                states_snapshot=["TX"],
                delivery_email="owner@example.com",
            )
        )
    session.add_all(batch)
    session.flush()
    assert [item.number for item in batch] == [1003, 1004, 1005]


def test_a_batch_number_never_changes(session):
    customer = Agent(slug="northstar", name="Northstar Insurance")
    session.add(customer)
    session.flush()
    item = make_request(session, customer, 10)
    session.commit()
    item.status = "pending"
    item.number = 2000
    with pytest.raises(ValueError, match="never changes"):
        session.flush()
    session.rollback()
    assert session.get(LeadRequest, item.id).number == 1001


def test_customer_request_contracts_carry_the_batch_number(session):
    from jawnix.customer_requests import request_detail
    from jawnix.schemas import RequestOut

    customer = Agent(slug="northstar", name="Northstar Insurance")
    session.add(customer)
    session.flush()
    item = make_request(session, customer, 10)
    assert request_detail(item).number == item.number == 1001
    assert RequestOut.model_validate(item).number == 1001
