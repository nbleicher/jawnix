"""Allocation controls: Niche Policy, Niche Assignment, Cooldown Window (#155).

Seam: admin HTTP API in; eligibility observed through inventory_count —
same style as the exclusion and eligibility suites.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jawnix.allocation import eligible_query, inventory_count
from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    AuditEntry,
    DistributionEvent,
    Job,
    Lead,
    LeadRequest,
    ListingObservation,
    NicheAssignment,
    RequestStatus,
    SourceNicheMapping,
)
from jawnix.worker import process_job


ADMIN_ID = uuid.uuid4()


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


def customer_with_request(
    session, *, slug: str, states: list[str] | None = None
):
    states = states or ["TX"]
    customer = Agent(slug=slug, name=slug.title(), licensed_states=states)
    request = LeadRequest(
        user_id=uuid.uuid4(),
        agent=customer,
        lead_count=10,
        states_snapshot=states,
        state_mode="all_saved",
        delivery_email=f"{slug}@example.com",
        status=RequestStatus.approved.value,
    )
    session.add_all([customer, request])
    session.flush()
    return customer, request


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


def test_customer_window_controls_receiver_eligibility(session, settings):
    short, short_request = customer_with_request(session, slug="short-window")
    long, long_request = customer_with_request(session, slug="long-window")
    prior = Agent(slug="prior-window", name="Prior", licensed_states=["TX"])
    received_at = datetime.now(timezone.utc) - timedelta(days=3)
    lead = Lead(
        phone="2145550100",
        title="Previously delivered",
        state="TX",
        last_distributed_at=received_at,
    )
    session.add_all([prior, lead])
    session.flush()
    session.add(
        DistributionEvent(
            lead_id=lead.id,
            agent_id=prior.id,
            delivered_at=received_at,
            source="test",
            phone=lead.phone,
            title=lead.title,
            state=lead.state,
        )
    )
    session.commit()

    client = as_admin(session, settings)
    try:
        assert client.put(
            f"/api/admin/customers/{short.id}/cooldown-window",
            json={"days": 1, "reason": "short freshness tier"},
        ).json() == {"days": 1}
        assert client.put(
            f"/api/admin/customers/{long.id}/cooldown-window",
            json={"days": 7, "reason": "long freshness tier"},
        ).json() == {"days": 7}
        session.expire_all()
        assert inventory_count(session, short_request, settings) == 1
        assert inventory_count(session, long_request, settings) == 0
        assert client.get(
            f"/api/admin/customers/{short.id}/cooldown-window"
        ).json() == {"days": 1}
        assert session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "cooldown_window_updated"
            )
        )
    finally:
        app.dependency_overrides.clear()


def test_adr_0019_smallest_window_sets_effective_exclusivity(session, settings):
    """A delivered Lead's protection equals the smallest other Customer's window."""
    recipient, _ = customer_with_request(session, slug="recipient")
    hungry, hungry_request = customer_with_request(session, slug="hungry")
    delivered_at = datetime.now(timezone.utc) - timedelta(days=3)
    lead = Lead(
        phone="2145550200",
        title="Shared pool",
        state="TX",
        last_distributed_at=delivered_at,
    )
    session.add(lead)
    session.flush()
    session.add(
        DistributionEvent(
            lead_id=lead.id,
            agent_id=recipient.id,
            delivered_at=delivered_at,
            source="test",
            phone=lead.phone,
            title=lead.title,
            state=lead.state,
        )
    )
    session.commit()

    client = as_admin(session, settings)
    try:
        assert (
            client.put(
                f"/api/admin/customers/{hungry.id}/cooldown-window",
                json={"days": 1, "reason": "Withdraw exclusivity floor"},
            ).status_code
            == 200
        )
        session.expire_all()
        assert inventory_count(session, hungry_request, settings) == 1
        assert (
            client.put(
                f"/api/admin/customers/{hungry.id}/cooldown-window",
                json={"days": 7, "reason": "Restore default window"},
            ).status_code
            == 200
        )
        session.expire_all()
        assert inventory_count(session, hungry_request, settings) == 0
    finally:
        app.dependency_overrides.clear()


def test_niche_policy_state_row_overrides_default_without_merging(
    session, settings
):
    customer, request = customer_with_request(
        session, slug="policy-override", states=["TX", "FL"]
    )
    truck_tx = mapped_lead(
        session,
        phone="2145550301",
        state="TX",
        segment="TX::truckers",
        niche="trucking",
    )
    roof_tx = mapped_lead(
        session,
        phone="2145550302",
        state="TX",
        segment="TX::roofers",
        niche="roofing",
    )
    truck_fl = mapped_lead(
        session,
        phone="3055550303",
        state="FL",
        segment="FL::truckers",
        niche="trucking",
    )
    roof_fl = mapped_lead(
        session,
        phone="3055550304",
        state="FL",
        segment="FL::roofers",
        niche="roofing",
    )
    session.commit()

    client = as_admin(session, settings)
    try:
        saved = client.put(
            f"/api/admin/customers/{customer.id}/niche-policy",
            json={
                "rows": [
                    {"state": None, "mode": "exclude", "niches": ["trucking"]},
                    {"state": "TX", "mode": "only", "niches": ["roofing"]},
                ],
                "reason": "TX roofing only; elsewhere bar trucking",
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["rows"] == [
            {"state": None, "mode": "exclude", "niches": ["trucking"]},
            {"state": "TX", "mode": "only", "niches": ["roofing"]},
        ]
        session.expire_all()
        assert inventory_count(session, request, settings) == 2
        phones = {
            lead.phone
            for lead in session.scalars(eligible_query(request, settings))
        }
        assert phones == {roof_tx.phone, roof_fl.phone}
        assert truck_tx.phone not in phones
        assert truck_fl.phone not in phones
        assert session.scalar(
            select(AuditEntry).where(AuditEntry.action == "niche_policy_updated")
        )
    finally:
        app.dependency_overrides.clear()


def test_only_rule_hides_unmapped_leads_and_projected_availability(
    session, settings
):
    customer, request = customer_with_request(
        session, slug="only-unmapped", states=["PA"]
    )
    mapped = mapped_lead(
        session,
        phone="2155550401",
        state="PA",
        segment="PA::roof repair",
        niche="roofing",
    )
    unmapped = Lead(phone="2155550402", title="Legacy Unknown", state="PA")
    session.add(unmapped)
    session.commit()

    client = as_admin(session, settings)
    try:
        draft = {
            "rows": [{"state": None, "mode": "only", "niches": ["roofing"]}]
        }
        preview = client.post(
            f"/api/admin/customers/{customer.id}/niche-policy/projected-availability",
            json=draft,
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["available"] == 1
        assert body["asOf"] is not None
        # Draft must not persist.
        assert client.get(
            f"/api/admin/customers/{customer.id}/niche-policy"
        ).json() == {"rows": []}

        assert (
            client.put(
                f"/api/admin/customers/{customer.id}/niche-policy",
                json={**draft, "reason": "Only roofing"},
            ).status_code
            == 200
        )
        session.expire_all()
        assert inventory_count(session, request, settings) == 1
        assert {
            lead.phone for lead in session.scalars(eligible_query(request, settings))
        } == {mapped.phone}
    finally:
        app.dependency_overrides.clear()


def test_niche_assignment_export_upload_and_segment_precedence(
    session, settings, monkeypatch
):
    customer, request = customer_with_request(
        session, slug="assign-precedence", states=["OH"]
    )
    mapped = mapped_lead(
        session,
        phone="6145550501",
        state="OH",
        segment="OH::dentists",
        niche="dental",
    )
    unmapped = Lead(phone="6145550502", title="Unmapped Clinic", state="OH")
    session.add(unmapped)
    session.commit()

    client = as_admin(session, settings)
    try:
        exported = client.get("/api/admin/niche-assignments/export")
        assert exported.status_code == 200, exported.text
        rows = list(csv.DictReader(io.StringIO(exported.text)))
        assert {"phone", "title", "state"} <= set(rows[0])
        phones = {row["phone"].strip('"') for row in rows}
        assert unmapped.phone in phones
        assert mapped.phone not in phones

        upload_body = (
            "phone,niche\n"
            f"{unmapped.phone},chiropractic\n"
            f"{mapped.phone},should-not-win\n"
        ).encode()
        uploaded = client.post(
            "/api/admin/niche-assignments",
            files={"file": ("niches.csv", upload_body, "text/csv")},
        )
        assert uploaded.status_code == 202, uploaded.text
        upload_id = uploaded.json()["id"]
        job = session.scalar(
            select(Job).where(Job.kind == "ingest_niche_assignments")
        )
        assert job is not None
        monkeypatch.setattr(
            "jawnix.worker.SessionLocal",
            sessionmaker(bind=session.bind, expire_on_commit=False),
        )
        monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
        process_job(job.id)
        session.expire_all()

        status = client.get(f"/api/admin/niche-assignments/{upload_id}")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["status"] == "complete"
        assert body["acceptedRows"] == 1
        assert body["skippedMappedRows"] == 1

        assert session.get(NicheAssignment, unmapped.phone).niche == "chiropractic"
        assert session.get(NicheAssignment, mapped.phone) is None

        # only dental → mapped dental passes; assigned chiropractic does not;
        # segment-derived dental still wins over any stamp attempt.
        assert (
            client.put(
                f"/api/admin/customers/{customer.id}/niche-policy",
                json={
                    "rows": [
                        {"state": None, "mode": "only", "niches": ["dental"]}
                    ],
                    "reason": "Dental only",
                },
            ).status_code
            == 200
        )
        session.expire_all()
        assert inventory_count(session, request, settings) == 1
        assert session.scalar(eligible_query(request, settings)).phone == mapped.phone

        assert (
            client.put(
                f"/api/admin/customers/{customer.id}/niche-policy",
                json={
                    "rows": [
                        {
                            "state": None,
                            "mode": "only",
                            "niches": ["chiropractic"],
                        }
                    ],
                    "reason": "Chiropractic only",
                },
            ).status_code
            == 200
        )
        session.expire_all()
        assert inventory_count(session, request, settings) == 1
        assert (
            session.scalar(eligible_query(request, settings)).phone
            == unmapped.phone
        )
    finally:
        app.dependency_overrides.clear()
