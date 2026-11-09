"""Batch numbers on real PostgreSQL: the sequence, the backfill, concurrency."""

from __future__ import annotations

import importlib.util
import os
import threading
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from jawnix.database import Base
from jawnix.job_wakeup import postgres_dsn
from jawnix.models import Agent, CustomerProfile, LeadRequest

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires disposable PostgreSQL"
)


@pytest.fixture
def database_url():
    base = make_url(DATABASE_URL)
    assert base.database.endswith("_test"), "Use a disposable *_test database"
    name = f"jawnix_{uuid.uuid4().hex[:16]}_test"
    with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261109_0054_batch_numbers.py"
    )
    spec = importlib.util.spec_from_file_location("batch_number_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_concurrent_submissions_never_share_a_number(database_url):
    engine = create_engine(database_url, pool_size=10)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        customer = Agent(slug="northstar", name="Northstar Insurance")
        db.add(customer)
        db.commit()
        customer_id = customer.id

    numbers: list[int] = []
    errors: list[BaseException] = []
    start = threading.Barrier(8)
    lock = threading.Lock()

    def submit():
        try:
            with factory() as db:
                user_id = uuid.uuid4()
                db.add(
                    CustomerProfile(
                        user_id=user_id,
                        email=f"{user_id}@example.com",
                        agent_id=customer_id,
                    )
                )
                db.flush()
                item = LeadRequest(
                    user_id=user_id,
                    agent_id=customer_id,
                    lead_count=10,
                    states_snapshot=["TX"],
                    delivery_email="owner@example.com",
                )
                db.add(item)
                start.wait()
                db.commit()
                with lock:
                    numbers.append(item.number)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    engine.dispose()
    assert not errors
    assert sorted(numbers) == list(range(1001, 1009))


def test_migration_backfills_and_continues_the_sequence(database_url):
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE lead_requests (id uuid PRIMARY KEY, "
                "created_at timestamptz NOT NULL, lead_count integer NOT NULL)"
            )
        )
        for day in (3, 1, 2):
            connection.execute(
                text(
                    "INSERT INTO lead_requests VALUES "
                    "(gen_random_uuid(), :at, 10)"
                ),
                {"at": f"2026-07-0{day}T00:00:00Z"},
            )
        with Operations.context(MigrationContext.configure(connection)):
            _migration().upgrade()
        assert connection.execute(
            text("SELECT number FROM lead_requests ORDER BY created_at")
        ).scalars().all() == [1001, 1002, 1003]
        assert connection.execute(
            text("SELECT nextval('lead_requests_number_seq')")
        ).scalar() == 1004
    engine.dispose()


def test_batch_list_search_and_today_queries_run_on_postgresql(database_url):
    """The UUID-prefix cast, ILIKE escaping, open-first ordering, and the
    correlated database probe are dialect-sensitive, so run them for real."""
    from datetime import datetime, timedelta, timezone

    from jawnix.batches import list_batches
    from jawnix.models import BatchArtifact, DistributionEvent, Lead, LeadUpload
    from jawnix.search import search
    from jawnix.today import today

    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with factory() as db:
        db1 = Agent(slug="northstar", name="Northstar 100% Insurance")
        db2 = Agent(slug="keystone", name="Keystone Medicare", lead_database="db2")
        db.add_all([db1, db2])
        db.flush()
        items = []
        for owner, status, hours in (
            (db1, "pending", 2),
            (db1, "waiting_inventory", 50),
            (db2, "delivered", 900),
            (db1, "failed", 3),
        ):
            user_id = uuid.uuid4()
            db.add(CustomerProfile(user_id=user_id, email=f"{user_id}@example.com", agent=owner))
            db.flush()
            item = LeadRequest(
                user_id=user_id,
                agent=owner,
                lead_count=10,
                states_snapshot=["FL"],
                delivery_email="owner@example.com",
                status=status,
                created_at=now - timedelta(hours=hours),
            )
            db.add(item)
            db.flush()
            items.append(item)
        pending, waiting, delivered, failed = items
        db.add(
            BatchArtifact(
                request_id=delivered.id, path="/x", filename="x.csv", row_count=10,
                parts=[], byte_count=1, sha256="a" * 64,
                expires_at=now - timedelta(days=1),
            )
        )
        lead = Lead(phone="2155550100", title="Dental", state="FL")
        db.add(lead)
        db.flush()
        db.add(DistributionEvent(lead_id=lead.id, agent_id=db2.id, request_id=delivered.id, inventory_database="db2"))
        db.add(LeadUpload(operator_id=uuid.uuid4(), filename="fl_agents.csv", state="FL", path="/x"))
        db.commit()

        assert [row["number"] for row in list_batches(db)["items"]] == [
            waiting.number, failed.number, pending.number,
        ]
        assert list_batches(db, status="expired")["items"][0]["number"] == delivered.number
        assert [row["number"] for row in list_batches(db, status="all", database="db2")["items"]] == [
            delivered.number
        ]
        assert {row["number"] for row in list_batches(db, needs_decision=True)["items"]} == {
            pending.number, failed.number,
        }
        prefix = str(delivered.id)[:13]
        assert [
            row["number"] for row in list_batches(db, status="all", q=f"REQ-{prefix}")["items"]
        ] == [delivered.number]
        assert list_batches(db, status="all", q="100%")["total"] == 3
        assert list_batches(db, status="all", q="0_I")["total"] == 0

        found = search(db, f"#{pending.number}")
        assert found["groups"][0]["results"][0]["href"] == f"/app/admin/batches/{pending.number}"
        assert [group["key"] for group in search(db, "keystone")["groups"]] == ["customers"]
        assert [group["key"] for group in search(db, "fl_agents")["groups"]] == ["uploads"]

        result = today(db, delivered_since=now - timedelta(hours=12), now=now)
        assert result["decisions"]["total"] == result["numbers"].decisions.waiting == 2
        assert [row["number"] for row in result["batches"]["inProgress"]] == [waiting.number]
    engine.dispose()
