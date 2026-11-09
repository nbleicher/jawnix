"""Insights (P6): the operator's weekly review.

Seam: seeded Batch Requests, ledger entries, Lead Reports, nightly snapshots
in; ``operator_insights`` and ``GET /api/admin/insights`` out.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.insights import operator_insights
from jawnix.models import (
    Agent,
    CreditLedgerEntry,
    CustomerAvailabilitySnapshot,
    CustomerProfile,
    DailySourcePerformance,
    DistributionEvent,
    Lead,
    LeadDispositionTransition,
    LeadReport,
    LeadRequest,
    NightlyReview,
)


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def customer(session, slug: str) -> Agent:
    item = Agent(slug=slug, name=slug.replace("-", " ").title())
    session.add(item)
    session.flush()
    return item


def batch(
    session,
    owner: Agent,
    status: str,
    *,
    lead_count: int = 100,
    delivered_at: datetime | None = None,
) -> LeadRequest:
    user_id = uuid.uuid4()
    session.add(
        CustomerProfile(
            user_id=user_id,
            email=f"{user_id}@example.com",
            licensed_states=["TX"],
            agent=owner,
        )
    )
    session.flush()
    item = LeadRequest(
        user_id=user_id,
        agent=owner,
        lead_count=lead_count,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email="ops@example.com",
        status=status,
        created_at=(delivered_at or NOW) - timedelta(hours=2),
        delivered_at=delivered_at,
    )
    session.add(item)
    session.flush()
    return item


def ledger(session, owner: Agent, kind: str, cents: int, created_at: datetime):
    charged = (
        batch(session, owner, "rejected")
        if kind == "batch_charge"
        else None
    )
    session.add(
        CreditLedgerEntry(
            customer_id=owner.id,
            kind=kind,
            amount_cents=cents,
            reason="Fixture",
            actor_user_id="system:test",
            batch_request_id=charged.id if charged else None,
            created_at=created_at,
        )
    )
    session.flush()


def delivery_event(session, owner: Agent, phone: str) -> DistributionEvent:
    lead = Lead(phone=phone, title="Fixture", state="TX")
    session.add(lead)
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        agent_id=owner.id,
        customer_name=owner.name,
        delivered_at=NOW - timedelta(days=5),
        source="test",
    )
    session.add(event)
    session.flush()
    return event


def report(session, owner: Agent, phone: str, created_at: datetime) -> None:
    event = delivery_event(session, owner, phone)
    session.add(
        LeadReport(
            distribution_event_id=event.id,
            customer_id=owner.id,
            reason="invalid_phone",
            created_at=created_at,
        )
    )
    session.flush()


def test_an_empty_platform_has_zeroes_and_no_rates(session):
    insights = operator_insights(session, now=NOW)

    assert insights["windowDays"] == 30
    assert insights["creditsUsed"] == {"cents": 0, "priorCents": 0}
    assert insights["creditsBought"]["purchases"] == 0
    assert insights["delivered"]["leads"] == 0
    assert insights["leadReports"] == {"count": 0, "priorCount": 0}
    assert insights["revenueByMonth"] == []
    assert insights["customerTrends"] == []
    assert insights["sourcePerformance"]["rows"] == []
    assert insights["sourcePerformance"]["snapshotDate"] is None
    assert insights["nightlyReviews"] == []


def test_money_and_deliveries_compare_the_last_30_days_with_the_30_before(
    session,
):
    northstar = customer(session, "northstar")
    ledger(session, northstar, "batch_charge", -12_500, NOW - timedelta(days=2))
    ledger(session, northstar, "batch_charge", -10_000, NOW - timedelta(days=40))
    ledger(session, northstar, "batch_charge", -99_999, NOW - timedelta(days=70))
    ledger(session, northstar, "purchase", 50_000, NOW - timedelta(days=1))
    ledger(session, northstar, "purchase", 20_000, NOW - timedelta(days=3))
    ledger(session, northstar, "purchase", 70_000, NOW - timedelta(days=31))
    # An adjustment is neither used nor bought.
    ledger(session, northstar, "admin_adjustment", 5_000, NOW - timedelta(days=1))
    batch(session, northstar, "delivered", lead_count=25_000, delivered_at=NOW - timedelta(days=1))
    batch(session, northstar, "delivered", lead_count=50_000, delivered_at=NOW - timedelta(days=45))
    batch(session, northstar, "approved", lead_count=9_999)
    report(session, northstar, "2145550001", NOW - timedelta(days=4))
    report(session, northstar, "2145550002", NOW - timedelta(days=35))
    session.commit()

    insights = operator_insights(session, now=NOW)

    assert insights["creditsUsed"] == {"cents": 12_500, "priorCents": 10_000}
    assert insights["creditsBought"] == {
        "cents": 70_000,
        "priorCents": 70_000,
        "purchases": 2,
        "priorPurchases": 1,
    }
    assert insights["delivered"] == {
        "leads": 25_000,
        "priorLeads": 50_000,
        "batches": 1,
        "priorBatches": 1,
    }
    assert insights["leadReports"] == {"count": 1, "priorCount": 1}


def test_revenue_is_grouped_by_calendar_month_from_the_first_active_one(
    session,
):
    harbor = customer(session, "harbor")
    ledger(session, harbor, "batch_charge", -41_200, datetime(2026, 10, 12, tzinfo=timezone.utc))
    ledger(session, harbor, "batch_charge", -34_500, datetime(2026, 11, 3, tzinfo=timezone.utc))
    batch(session, harbor, "delivered", lead_count=1_210_000, delivered_at=datetime(2026, 9, 20, tzinfo=timezone.utc))
    batch(session, harbor, "delivered", lead_count=1_824_000, delivered_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
    batch(session, harbor, "delivered", lead_count=690_000, delivered_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    session.commit()

    months = operator_insights(session, now=NOW)["revenueByMonth"]

    assert [month["month"] for month in months] == ["2026-09", "2026-10", "2026-11"]
    assert [month["creditsUsedCents"] for month in months] == [0, 41_200, 34_500]
    assert [month["leadsDelivered"] for month in months] == [1_210_000, 1_824_000, 690_000]
    assert [month["daysSoFar"] for month in months] == [None, None, 9]


def test_customer_trends_rank_by_delivered_with_reports_and_shortfalls(session):
    summit = customer(session, "summit-benefits")
    gulfshore = customer(session, "gulfshore")
    ridgeline = customer(session, "ridgeline")
    batch(session, summit, "delivered", lead_count=450_000, delivered_at=NOW - timedelta(days=3))
    batch(session, summit, "delivered", lead_count=300_000, delivered_at=NOW - timedelta(days=40))
    batch(session, gulfshore, "delivered", lead_count=69_666, delivered_at=NOW - timedelta(days=10))
    batch(session, gulfshore, "delivered", lead_count=139_332, delivered_at=NOW - timedelta(days=50))
    report(session, gulfshore, "2145550003", NOW - timedelta(days=2))
    waiting = batch(session, ridgeline, "waiting_inventory", lead_count=100_000)
    session.add(
        CustomerAvailabilitySnapshot(
            customer_id=ridgeline.id, available=68_410, computed_at=NOW
        )
    )
    session.commit()
    assert waiting.available_count is None

    trends = operator_insights(session, now=NOW)["customerTrends"]

    assert [row["customer"]["name"] for row in trends] == [
        "Summit Benefits",
        "Gulfshore",
        "Ridgeline",
    ]
    assert trends[0]["delivered"] == 450_000
    assert trends[0]["priorDelivered"] == 300_000
    assert trends[0]["customer"]["href"] == f"/app/admin/customers/{summit.id}"
    assert trends[1]["reports"] == 1
    assert trends[2]["shortBy"] == 31_590
    assert trends[0]["shortBy"] is None


def test_source_performance_shows_no_rate_without_outcomes(session):
    review = NightlyReview(review_date=date(2026, 11, 9), status="complete")
    session.add(review)
    session.flush()
    empty = {
        "delivered": 0, "worked": 0, "rated": 0, "good": 0, "poor": 0,
        "positive": 0, "booked": 0,
    }
    for index, (segment, delivered, worked, positive) in enumerate(
        (
            ("TN::dentist", 84_210, 0, 0),
            ("TX::roofing", 77_880, 10, 4),
            ("FL::chiropractor", 70_040, 0, 0),
        )
    ):
        state, _, keyword = segment.partition("::")
        session.add(
            DailySourcePerformance(
                nightly_review_id=review.id,
                snapshot_date=date(2026, 11, 9),
                segment_key=segment,
                state=state,
                keyword=keyword,
                niche="",
                window_started_at=NOW - timedelta(days=90),
                window_ended_at=NOW,
                counts={**empty, "delivered": delivered, "worked": worked, "positive": positive},
                rates={"positiveResponse": 0.0},
                eligibility="unconfirmed_niche",
                evidence_checksum=f"checksum-{index}",
            )
        )
    owner = customer(session, "northstar")
    event = delivery_event(session, owner, "2145550009")
    session.add(
        LeadDispositionTransition(
            distribution_event_id=event.id,
            customer_id=owner.id,
            actor_user_id=uuid.uuid4(),
            disposition="positive_response",
        )
    )
    session.commit()

    performance = operator_insights(session, now=NOW)["sourcePerformance"]

    assert performance["snapshotDate"] == "2026-11-09"
    assert performance["windowDays"] == 90
    assert performance["segments"] == 3
    assert performance["outcomesReported"] == 1
    rows = performance["rows"]
    assert [row["segment"] for row in rows] == ["TN::dentist", "TX::roofing", "FL::chiropractor"]
    assert rows[0]["positiveRate"] is None
    assert rows[0]["poorRate"] is None
    assert rows[1]["positiveRate"] == 0.4
    assert rows[1]["bookedRate"] == 0.0


def test_the_insights_endpoint_is_admin_only(session, settings):
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(), email="admin@example.com", role="admin", csrf="t"
    )
    try:
        response = TestClient(app).get("/api/admin/insights")
        assert response.status_code == 200
        assert response.json()["windowDays"] == 30
    finally:
        app.dependency_overrides.clear()

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="customer@example.com",
        role="customer",
        audience="customer",
        csrf="t",
    )
    try:
        assert TestClient(app).get("/api/admin/insights").status_code == 403
    finally:
        app.dependency_overrides.clear()
