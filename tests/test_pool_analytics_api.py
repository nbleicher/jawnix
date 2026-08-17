"""Admin-only pool analytics (#155 T9).

Seam: admin HTTP API in; seeded inventory scenarios out. Counts are never
computed on a plain GET (page-load path) — only on explicit refresh / draft
projection. Eligibility semantics match allocation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.pool_analytics import compute_cooldown_forecast
from jawnix.models import (
    Agent,
    DistributionEvent,
    ExclusionList,
    ExclusionPhone,
    Lead,
    ListingObservation,
    NicheAssignment,
    SourceNicheMapping,
)


ADMIN_ID = uuid.uuid4()
AS_OF = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)


def as_admin(session, settings=None):
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=ADMIN_ID,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def mapped_lead(
    session, *, phone: str, state: str, segment: str, niche: str, title: str = ""
):
    lead = Lead(phone=phone, title=title or f"{niche} {state}", state=state)
    session.add(lead)
    session.flush()
    observation = ListingObservation(
        lead_id=lead.id,
        dataset_checksum=uuid.uuid4().hex * 2,
        row_number=int(phone[-4:]),
        normalized_phone=phone,
        title=lead.title,
        state=state,
        source=segment,
        niche=niche,
        valid=True,
        observed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    session.add(observation)
    session.flush()
    lead.current_listing_observation_id = observation.id
    if (
        session.scalar(
            select(SourceNicheMapping).where(
                SourceNicheMapping.segment_key == segment
            )
        )
        is None
    ):
        code, _, keyword = segment.partition("::")
        session.add(
            SourceNicheMapping(
                segment_key=segment,
                state=code or state,
                keyword=keyword or segment,
                niche=niche,
                confirmed=True,
            )
        )
    return lead


def bare_lead(session, *, phone: str, state: str, title: str = "Bare"):
    lead = Lead(phone=phone, title=title, state=state)
    session.add(lead)
    session.flush()
    return lead


def test_pool_breakdown_groups_by_state_and_niche_with_unmapped_bucket(
    session, settings
):
    mapped_lead(
        session,
        phone="2145550101",
        state="TX",
        segment="TX::dentist",
        niche="Dental",
    )
    mapped_lead(
        session,
        phone="2145550102",
        state="TX",
        segment="TX::dentist",
        niche="Dental",
    )
    mapped_lead(
        session,
        phone="3055550103",
        state="FL",
        segment="FL::chiro",
        niche="Chiropractic",
    )
    bare_lead(session, phone="2145550104", state="TX", title="No niche")
    # Manual Niche Assignment still counts under that niche, not unmapped.
    assigned = bare_lead(session, phone="2145550105", state="FL", title="Assigned")
    session.add(NicheAssignment(phone=assigned.phone, niche="Dental"))
    suppressed = bare_lead(session, phone="2145550106", state="TX", title="Suppressed")
    suppressed.suppressed = True
    session.commit()

    client = as_admin(session, settings)
    try:
        empty = client.get("/api/admin/pool-breakdown").json()
        assert empty == {"asOf": None, "cells": []}

        refreshed = client.post("/api/admin/pool-breakdown/refresh").json()
        assert refreshed["asOf"] is not None
        cells = {
            (cell["state"], cell["niche"]): cell["count"]
            for cell in refreshed["cells"]
        }
        assert cells == {
            ("FL", "Chiropractic"): 1,
            ("FL", "Dental"): 1,
            ("TX", "Dental"): 2,
            ("TX", "unmapped"): 1,
        }

        cached = client.get("/api/admin/pool-breakdown").json()
        assert cached["asOf"] == refreshed["asOf"]
        assert cached["cells"] == refreshed["cells"]
    finally:
        app.dependency_overrides.clear()


def test_pool_breakdown_subtracts_global_exclusions(session, settings):
    excluded = mapped_lead(
        session,
        phone="2145550191",
        state="TX",
        segment="TX::excluded-dentist",
        niche="Dental",
    )
    mapped_lead(
        session,
        phone="2145550192",
        state="TX",
        segment="TX::included-dentist",
        niche="Dental",
    )
    exclusion = ExclusionList(
        customer_id=None,
        uploaded_by="admin",
        exclusion_type="dnc",
        filename="global.csv",
        storage_path="/tmp/global.csv",
        status="confirmed",
        global_effective=True,
    )
    session.add(exclusion)
    session.flush()
    session.add(
        ExclusionPhone(exclusion_list_id=exclusion.id, phone=excluded.phone)
    )
    session.commit()

    client = as_admin(session, settings)
    try:
        cells = client.post("/api/admin/pool-breakdown/refresh").json()["cells"]
        assert cells == [{"state": "TX", "niche": "Dental", "count": 1}]
    finally:
        app.dependency_overrides.clear()


def test_customer_availability_honors_filters_and_is_cached_with_as_of(
    session, settings, monkeypatch
):
    monkeypatch.setattr(
        "jawnix.pool_analytics.utcnow",
        lambda: AS_OF,
    )
    customer = Agent(
        slug="avail-customer",
        name="Avail",
        licensed_states=["TX"],
        cooldown_window_days=7,
    )
    other = Agent(slug="other-avail", name="Other", licensed_states=["TX"])
    session.add_all([customer, other])
    session.flush()

    # Eligible today in TX Dental.
    mapped_lead(
        session,
        phone="2145550201",
        state="TX",
        segment="TX::dental-a",
        niche="Dental",
    )
    # Wrong state for Licensed States.
    mapped_lead(
        session,
        phone="3055550202",
        state="FL",
        segment="FL::dental-b",
        niche="Dental",
    )
    # Previously sent to this Customer — permanent no-repeat.
    prior = mapped_lead(
        session,
        phone="2145550203",
        state="TX",
        segment="TX::dental-c",
        niche="Dental",
    )
    session.add(
        DistributionEvent(
            lead_id=prior.id,
            agent_id=customer.id,
            delivered_at=AS_OF - timedelta(days=30),
            source="test",
            phone=prior.phone,
            title=prior.title,
            state=prior.state,
        )
    )
    prior.last_distributed_at = AS_OF - timedelta(days=30)
    # Inside this Customer's Cooldown Window (received by someone else 3 days ago).
    cooling = mapped_lead(
        session,
        phone="2145550204",
        state="TX",
        segment="TX::dental-d",
        niche="Dental",
    )
    cooling.last_distributed_at = AS_OF - timedelta(days=3)
    session.add(
        DistributionEvent(
            lead_id=cooling.id,
            agent_id=other.id,
            delivered_at=cooling.last_distributed_at,
            source="test",
            phone=cooling.phone,
            title=cooling.title,
            state=cooling.state,
        )
    )
    # Excluded for this Customer.
    excluded = mapped_lead(
        session,
        phone="2145550205",
        state="TX",
        segment="TX::dental-e",
        niche="Dental",
    )
    exclusion = ExclusionList(
        customer_id=customer.id,
        exclusion_type="dnc",
        filename="block.csv",
        storage_path="/tmp/block.csv",
        status="complete",
        global_effective=False,
        uploaded_by="admin",
    )
    session.add(exclusion)
    session.flush()
    session.add(ExclusionPhone(exclusion_list_id=exclusion.id, phone=excluded.phone))
    # Niche Policy will exclude Chiropractic once set.
    mapped_lead(
        session,
        phone="2145550206",
        state="TX",
        segment="TX::chiro-a",
        niche="Chiropractic",
    )
    session.commit()

    client = as_admin(session, settings)
    try:
        assert client.put(
            f"/api/admin/customers/{customer.id}/niche-policy",
            json={
                "rows": [
                    {"state": None, "mode": "exclude", "niches": ["Chiropractic"]}
                ],
                "reason": "no chiro",
            },
        ).status_code == 200

        empty = client.get(
            f"/api/admin/customers/{customer.id}/availability"
        ).json()
        assert empty == {
            "asOf": None,
            "available": None,
            "forecast": None,
        }

        refreshed = client.post(
            f"/api/admin/customers/{customer.id}/availability/refresh"
        ).json()
        assert refreshed["available"] == 1
        assert refreshed["asOf"] is not None
        assert len(refreshed["forecast"]) == 15
        assert refreshed["forecast"][0] == {
            "offset": 0,
            "date": refreshed["forecast"][0]["date"],
            "count": 1,
        }

        cached = client.get(
            f"/api/admin/customers/{customer.id}/availability"
        ).json()
        assert cached == refreshed
    finally:
        app.dependency_overrides.clear()


def test_cooldown_forecast_counts_leads_becoming_eligible_per_day(
    session, settings, monkeypatch
):
    monkeypatch.setattr(
        "jawnix.pool_analytics.utcnow",
        lambda: AS_OF,
    )
    customer = Agent(
        slug="forecast-customer",
        name="Forecast",
        licensed_states=["TX"],
        cooldown_window_days=7,
    )
    prior = Agent(slug="forecast-prior", name="Prior", licensed_states=["TX"])
    session.add_all([customer, prior])
    session.flush()

    # Eligible today (never distributed).
    mapped_lead(
        session,
        phone="2145550301",
        state="TX",
        segment="TX::f1",
        niche="Dental",
    )
    # Window lapses in 2 days: distributed 5 days ago with a 7-day window.
    day2 = mapped_lead(
        session,
        phone="2145550302",
        state="TX",
        segment="TX::f2",
        niche="Dental",
    )
    day2.last_distributed_at = AS_OF - timedelta(days=5)
    session.add(
        DistributionEvent(
            lead_id=day2.id,
            agent_id=prior.id,
            delivered_at=day2.last_distributed_at,
            source="test",
            phone=day2.phone,
            title=day2.title,
            state=day2.state,
        )
    )
    # Window lapses in 4 days.
    day4 = mapped_lead(
        session,
        phone="2145550303",
        state="TX",
        segment="TX::f3",
        niche="Dental",
    )
    day4.last_distributed_at = AS_OF - timedelta(days=3)
    session.add(
        DistributionEvent(
            lead_id=day4.id,
            agent_id=prior.id,
            delivered_at=day4.last_distributed_at,
            source="test",
            phone=day4.phone,
            title=day4.title,
            state=day4.state,
        )
    )
    # Same Customer history — never becomes eligible again.
    blocked = mapped_lead(
        session,
        phone="2145550304",
        state="TX",
        segment="TX::f4",
        niche="Dental",
    )
    blocked.last_distributed_at = AS_OF - timedelta(days=5)
    session.add(
        DistributionEvent(
            lead_id=blocked.id,
            agent_id=customer.id,
            delivered_at=blocked.last_distributed_at,
            source="test",
            phone=blocked.phone,
            title=blocked.title,
            state=blocked.state,
        )
    )
    session.commit()

    client = as_admin(session, settings)
    try:
        payload = client.post(
            f"/api/admin/customers/{customer.id}/availability/refresh"
        ).json()
        by_offset = {row["offset"]: row["count"] for row in payload["forecast"]}
        assert by_offset[0] == 1
        assert by_offset[2] == 1
        assert by_offset[4] == 1
        assert sum(by_offset.values()) == 3
        assert payload["available"] == 1
        assert payload["forecast"][2]["date"] == "2026-08-05"
        assert payload["forecast"][4]["date"] == "2026-08-07"
    finally:
        app.dependency_overrides.clear()


def test_cooldown_forecast_counts_leads_lapsing_later_today_in_day_zero(
    session, settings
):
    # Pinned mid-day: a wall-clock `as_of` after 22:00 UTC would push a lapse
    # two hours out into tomorrow and the case under test would not exist.
    as_of = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
    customer = Agent(
        slug="forecast-day-zero",
        name="Forecast day zero",
        licensed_states=["TX"],
        cooldown_window_days=7,
    )
    prior = Agent(slug="forecast-day-zero-prior", name="Prior")
    session.add_all([customer, prior])
    session.flush()
    lead = mapped_lead(
        session,
        phone="2145550391",
        state="TX",
        segment="TX::day-zero",
        niche="Dental",
    )
    lead.last_distributed_at = as_of - timedelta(days=7) + timedelta(hours=2)
    session.add(
        DistributionEvent(
            lead_id=lead.id,
            agent_id=prior.id,
            delivered_at=lead.last_distributed_at,
            source="test",
            phone=lead.phone,
            title=lead.title,
            state=lead.state,
        )
    )
    session.commit()

    available, forecast = compute_cooldown_forecast(
        session,
        customer,
        settings,
        as_of=as_of,
    )
    assert available == 0
    assert forecast[0]["count"] == 1


def test_projected_availability_and_exclusion_reuse_availability_figure(
    session, settings
):
    customer = Agent(
        slug="preview-customer",
        name="Preview",
        licensed_states=["TX"],
        cooldown_window_days=7,
    )
    session.add(customer)
    session.flush()
    mapped_lead(
        session,
        phone="2145550401",
        state="TX",
        segment="TX::p1",
        niche="Dental",
    )
    mapped_lead(
        session,
        phone="2145550402",
        state="TX",
        segment="TX::p2",
        niche="Chiropractic",
    )
    unmapped = bare_lead(session, phone="2145550403", state="TX")
    session.add(NicheAssignment(phone=unmapped.phone, niche="Dental"))
    session.commit()

    client = as_admin(session, settings)
    try:
        refreshed = client.post(
            f"/api/admin/customers/{customer.id}/availability/refresh"
        ).json()
        assert refreshed["available"] == 3

        projection = client.post(
            f"/api/admin/customers/{customer.id}/niche-policy/projected-availability",
            json={
                "rows": [
                    {"state": None, "mode": "only", "niches": ["Dental"]}
                ]
            },
        ).json()
        assert projection["available"] == 2
        assert projection["asOf"] is not None

        # Exclusion confirmation surfaces reuse the cached availability number.
        exclusion = ExclusionList(
            customer_id=customer.id,
            exclusion_type="dnc",
            filename="pending.csv",
            storage_path="/tmp/pending.csv",
            status="pending_confirmation",
            global_effective=False,
            uploaded_by="admin",
            pool_impact=1,
        )
        session.add(exclusion)
        session.commit()

        status = client.get(
            f"/api/admin/exclusion-lists/{exclusion.id}"
        ).json()
        assert status["customerAvailability"] == {
            "available": refreshed["available"],
            "asOf": refreshed["asOf"],
        }
    finally:
        app.dependency_overrides.clear()
