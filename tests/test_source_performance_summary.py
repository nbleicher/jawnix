"""Admin Source Performance must not scan every DistributionEvent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event, insert, select

from jawnix.api import app, get_db
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.models import Agent, DistributionEvent, Lead, LeadOutcome
from jawnix.performance import (
    keyword_outcome_analytics,
    source_performance_snapshot,
    source_performance_summary,
)


def test_source_performance_summary_matches_snapshot_globals(session):
    customer = Agent(slug="summary-customer", name="Summary Customer")
    session.add(customer)
    session.flush()
    lead = Lead(phone="5125550100", title="Roof Co", state="TX")
    session.add(lead)
    session.flush()
    google = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state="TX",
        source_kind="google_maps",
        source_segment_key="TX::roof repair",
        source_niche="Roofing",
        distribution_period="2026-08",
        delivered_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        source="summary-test",
    )
    legacy = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state="TX",
        source_kind="legacy",
        source_segment_key="",
        source_niche="",
        distribution_period="2026-07",
        delivered_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        source="summary-test-legacy",
    )
    session.add_all([google, legacy])
    session.flush()
    session.add(
        LeadOutcome(
            distribution_event_id=google.id,
            customer_id=customer.id,
            kind="good",
            metric="quality",
        )
    )
    session.add(
        LeadOutcome(
            distribution_event_id=google.id,
            customer_id=customer.id,
            kind="positive_response",
            metric="positive_response",
        )
    )
    session.flush()

    summary = source_performance_summary(session)
    full = source_performance_snapshot(session)
    assert summary["global"] == full["global"]
    assert summary["legacy"] == full["legacy"]
    assert summary["cohorts"] == []


def test_admin_source_performance_does_not_call_full_snapshot(
    session, monkeypatch
):
    def boom(_db):
        raise AssertionError(
            "source_performance_snapshot must not run on the Admin HTTP path"
        )

    monkeypatch.setattr(
        "jawnix.performance.source_performance_snapshot", boom
    )

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        response = TestClient(app).get("/api/admin/source-performance")
        assert response.status_code == 200
        body = response.json()
        assert body["cohorts"] == []
        assert "global" in body
        assert "rows" in body
    finally:
        app.dependency_overrides.clear()


def test_keyword_outcome_analytics_never_binds_event_ids_as_parameters(session):
    """Outcome / event ids must stay inside SQL subqueries.

    The Keywords workspace used to load every active LeadOutcome id into
    Python and bind each as an ``IN`` parameter — the same ceiling that
    500ed Agency assignment preview once history crossed 65,535 ids.
    """
    customer = Agent(slug="kw-params", name="Keyword Params")
    session.add(customer)
    session.flush()
    session.execute(
        insert(Lead),
        [
            {
                "phone": f"512{n:07d}",
                "title": f"Biz {n}",
                "state": "TX",
            }
            for n in range(200)
        ],
    )
    lead_ids = list(session.scalars(select(Lead.id)))
    session.execute(
        insert(DistributionEvent),
        [
            {
                "lead_id": lead_id,
                "agent_id": customer.id,
                "customer_name": customer.name,
                "phone": f"512{i:07d}",
                "title": f"Biz {i}",
                "state": "TX",
                "source_kind": "google_maps",
                "source_segment_key": "TX::roof repair",
                "source_niche": "Roofing",
                "distribution_period": "2026-08",
                "delivered_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
                "source": "kw-params",
            }
            for i, lead_id in enumerate(lead_ids)
        ],
    )
    event_ids = list(session.scalars(select(DistributionEvent.id)))
    session.execute(
        insert(LeadOutcome),
        [
            {
                "id": uuid.uuid4(),
                "distribution_event_id": event_id,
                "customer_id": customer.id,
                "kind": "positive_response",
                "metric": "positive_response",
            }
            for event_id in event_ids[:50]
        ],
    )
    session.commit()

    parameter_counts: list[int] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        parameter_counts.append(len(parameters or ()))

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        rows = keyword_outcome_analytics(session, keywords=["roof repair"])
    finally:
        event.remove(engine, "before_cursor_execute", record)

    roof = next(row for row in rows if row["keyword"] == "roof repair")
    assert roof["delivered"] == 200
    assert roof["positive"] == 50
    assert max(parameter_counts) < 100
