"""Insights, Setup, Agencies, and Activity reads on real PostgreSQL (A5).

The SQLite suite covers the rules; this runs the same queries where
PostgreSQL is stricter: grouping by an expression, ``COUNT ... FILTER``,
``UNION`` subqueries, escaped ``LIKE`` patterns, and UUIDs stored as native
values while Activity stores them as text.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from jawnix.activity import activity_filters, query_activity, record_activity
from jawnix.agency_management import agency_directory
from jawnix.config import Settings
from jawnix.database import Base
from jawnix.insights import operator_insights
from jawnix.job_wakeup import postgres_dsn
from jawnix.models import (
    Agency,
    Agent,
    CreditLedgerEntry,
    CustomerProfile,
    DailySourcePerformance,
    DistributionEvent,
    Lead,
    LeadDispositionTransition,
    LeadReport,
    LeadRequest,
    NightlyReview,
    SourceNicheMapping,
    UserAccount,
)
from jawnix.operator_setup import operator_setup

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires disposable PostgreSQL"
)

NOW = datetime.now(timezone.utc)


@pytest.fixture
def factory():
    base = make_url(DATABASE_URL)
    assert base.database.endswith("_test"), "Use a disposable *_test database"
    name = f"jawnix_{uuid.uuid4().hex[:16]}_test"
    with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = create_engine(
        base.set(database=name).render_as_string(hide_password=False)
    )
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _batch(db, owner: Agent, user_id: uuid.UUID, status: str, leads: int, delivered_at=None):
    item = LeadRequest(
        user_id=user_id,
        agent=owner,
        lead_count=leads,
        states_snapshot=["FL"],
        state_mode="all_saved",
        delivery_email="ops@example.com",
        status=status,
        delivered_at=delivered_at,
    )
    db.add(item)
    db.flush()
    return item


def _seed(db):
    agency = Agency(slug="gulf", name="Gulf Coast Agency")
    db.add(agency)
    db.flush()
    harbor = Agent(slug="harbor", name="Harbor Insurance", agency_id=agency.id)
    solo = Agent(slug="solo", name="Northstar Insurance")
    db.add_all([harbor, solo])
    db.flush()
    river = uuid.uuid4()
    db.add_all(
        [
            CustomerProfile(
                user_id=river,
                email="river@example.com",
                first_name="River",
                last_name="Morgan",
                agent_id=solo.id,
            ),
            UserAccount(auth_user_id=river, customer_id=solo.id, email="river@example.com"),
        ]
    )
    delivered = _batch(db, harbor, river, "delivered", 50_000, NOW - timedelta(days=2))
    _batch(db, harbor, river, "delivered", 25_000, NOW - timedelta(days=40))
    _batch(db, solo, river, "pending", 10_000)
    charge = _batch(db, solo, river, "rejected", 1)
    db.add_all(
        [
            CreditLedgerEntry(
                customer_id=solo.id,
                kind="batch_charge",
                amount_cents=-25_000,
                actor_user_id="system:allocation",
                batch_request_id=charge.id,
                created_at=NOW - timedelta(days=1),
            ),
            CreditLedgerEntry(
                customer_id=solo.id,
                kind="purchase",
                amount_cents=50_000,
                actor_user_id="system:stripe",
                created_at=NOW - timedelta(days=1),
            ),
        ]
    )
    lead = Lead(phone="2145550001", title="Fixture", state="FL")
    db.add(lead)
    db.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        agent_id=harbor.id,
        request_id=delivered.id,
        delivered_at=NOW - timedelta(days=2),
        source="request",
    )
    db.add(event)
    db.flush()
    db.add_all(
        [
            LeadReport(
                distribution_event_id=event.id,
                customer_id=harbor.id,
                reason="invalid_phone",
                created_at=NOW - timedelta(days=1),
            ),
            LeadDispositionTransition(
                distribution_event_id=event.id,
                customer_id=harbor.id,
                actor_user_id=river,
                disposition="positive_response",
            ),
        ]
    )
    review = NightlyReview(review_date=date.today(), status="complete")
    db.add(review)
    db.flush()
    db.add(
        DailySourcePerformance(
            nightly_review_id=review.id,
            snapshot_date=date.today(),
            segment_key="TX::roofing",
            state="TX",
            keyword="roofing",
            niche="Roofing",
            window_started_at=NOW - timedelta(days=90),
            window_ended_at=NOW,
            counts={"delivered": 100, "worked": 0, "rated": 0, "positive": 0, "booked": 0, "poor": 0},
            rates={},
            eligibility="unconfirmed_niche",
            evidence_checksum="checksum",
        )
    )
    db.add(
        SourceNicheMapping(
            segment_key="TX::roofing",
            state="TX",
            keyword="roofing",
            niche="Roofing",
            confirmed=False,
        )
    )
    for action, actor, target_type, target_id in (
        ("customer_hard_delete_refused", str(uuid.uuid4()), "customer", solo.id),
        ("licensed_states_update", str(river), "customer", solo.id),
        ("batch_request_approve", "telegram:1", "batch_request", delivered.id),
        ("source_niche_confirmed", "system:nightly-scheduler", "source_segment", "TX::roofing"),
    ):
        record_activity(
            db,
            action=action,
            target_type=target_type,
            target_id=target_id,
            actor_id=actor,
            reason="Fixture",
        )
    db.commit()
    return river, delivered


def test_operator_reads_run_on_postgres(factory):
    with factory() as db:
        river, delivered = _seed(db)

        insights = operator_insights(db)
        assert insights["creditsUsed"]["cents"] == 25_000
        assert insights["creditsBought"]["purchases"] == 1
        assert insights["delivered"]["leads"] == 50_000
        assert insights["delivered"]["priorLeads"] == 25_000
        assert insights["leadReports"]["count"] == 1
        assert insights["customerTrends"][0]["customer"]["name"] == "Harbor Insurance"
        assert insights["customerTrends"][0]["priorDelivered"] == 25_000
        assert insights["sourcePerformance"]["outcomesReported"] == 1
        assert insights["sourcePerformance"]["rows"][0]["positiveRate"] is None
        assert insights["revenueByMonth"][-1]["leadsDelivered"] >= 50_000

        setup = {area["key"]: area for area in operator_setup(db, Settings())["areas"]}
        assert setup["nicheMappings"]["state"] == "1 awaiting review"
        assert setup["agencies"]["state"] == "1 active · 1 member"

        directory = agency_directory(db)
        assert directory["agencies"][0]["delivered30d"] == 50_000
        assert directory["independent"][0]["openBatches"] == 1

        assert query_activity(db, refused=True)["total"] == 1
        assert query_activity(db, who="system")["total"] == 1
        assert query_activity(db, who="operator")["total"] == 2
        entries = query_activity(db, who=str(river))["entries"]
        assert entries[0]["actorName"] == "River Morgan"
        approved = query_activity(db, action="batch_request_approve")["entries"][0]
        assert approved["entityLabel"] == f"Batch #{delivered.number}"
        filters = activity_filters(db)
        assert {"value": str(river), "label": "River Morgan"} in filters["who"]
        assert any(item["value"] == "source_segment" for item in filters["entityTypes"])
