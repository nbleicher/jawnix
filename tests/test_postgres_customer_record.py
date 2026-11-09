"""The Customers table and Customer page reads on real PostgreSQL.

The SQLite suite covers the rules; this runs the same seeded reads against
PostgreSQL, where grouping, boolean aggregates, and UUID joins are stricter.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from jawnix.batches import list_batches
from jawnix.customer_directory import build_customer_directory
from jawnix.customer_record import customer_lead_reports, customer_summary
from jawnix.database import Base
from jawnix.job_wakeup import postgres_dsn
from test_customer_record import seeded  # noqa: F401 — shared fixture

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires disposable PostgreSQL"
)


@pytest.fixture
def session():
    base = make_url(DATABASE_URL)
    assert base.database.endswith("_test"), "Use a disposable *_test database"
    name = f"jawnix_{uuid.uuid4().hex[:16]}_test"
    with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = create_engine(base.set(database=name).render_as_string(hide_password=False))
    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as value:
            yield value
    finally:
        engine.dispose()
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def test_customers_table_reads_on_postgres(session, seeded):  # noqa: F811
    result = build_customer_directory(session)
    rows = {row.slug: row for row in result.customers}
    assert result.counts.active == 9
    assert result.counts.deactivated == 1
    assert result.customers[0].slug == "northstar-insurance"
    assert rows["northstar-insurance"].needs[0].label == f"Approve #{seeded['approval'].number}"
    assert rows["northstar-insurance"].balance_cents == 41_250
    assert rows["northstar-insurance"].held_for == [seeded["approval"].number]
    assert rows["harbor-insurance"].needs[0].label == "Conflict with Lakeside Brokers"
    assert rows["summit-benefits"].delivered_30d == 450_000
    assert rows["harbor-insurance"].available == 412_880
    billed = build_customer_directory(session, billing="billed", needs_only=True, database="db1")
    assert [row.slug for row in billed.customers] == ["northstar-insurance", "harbor-insurance"]


def test_customer_page_reads_on_postgres(session, seeded):  # noqa: F811
    summary = customer_summary(session, seeded["gulfshore"])
    assert summary["leadReports"] == {"open": 1, "total": 1}
    assert summary["delivered"]["leads"] == 69_666
    assert summary["needs"][0]["label"] == "Report · invalid phone"
    reports = customer_lead_reports(session, seeded["gulfshore"])
    assert reports[0]["batch"]["number"] == seeded["delivered"].number
    batches = list_batches(session, status="all", customer_id=seeded["northstar"].id)
    assert [row["number"] for row in batches["items"]] == [seeded["approval"].number]
    assert batches["items"][0]["cost"] == {"billed": True, "amountCents": 12_500, "hold": "active"}
