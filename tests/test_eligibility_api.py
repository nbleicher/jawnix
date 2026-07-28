"""The administrator Lead Report and eligibility control contracts (#58).

The controls sit beside the report, never on top of it. Every test here holds
the same line twice: the decision must take effect, and the Distribution Event
and Listing Observations it was made about must come out byte-identical.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.allocation import inventory_count
from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import Settings
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    AuditEntry,
    CustomerProfile,
    DistributionEvent,
    EligibilityHold,
    Lead,
    LeadCorrectionEvent,
    LeadReport,
    LeadRequest,
    ListingObservation,
    RequestStatus,
)

ADMIN_ID = uuid.uuid4()


def as_admin(session):
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=ADMIN_ID,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    return TestClient(app)


def reported_lead(
    session,
    *,
    disposition: str = "invalid_phone",
    with_observation: bool = False,
):
    """A delivered Lead a Customer has reported, held by an Eligibility Hold."""
    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"customer-{suffix}",
        name="Reporting Customer",
        licensed_states=["PA"],
    )
    lead = Lead(
        phone=f"2155{session.query(Lead).count():06d}",
        title="Delivered Title",
        state="PA",
        legacy_title="Imported Title",
        legacy_state="PA",
    )
    session.add_all([customer, lead])
    session.flush()
    if with_observation:
        observation = ListingObservation(
            lead_id=lead.id,
            dataset_checksum=uuid.uuid4().hex * 2,
            row_number=1,
            normalized_phone=lead.phone,
            title="Observed Title",
            state="PA",
            source="roofing-pa",
            niche="Roofing",
            valid=True,
            observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        session.add(observation)
        session.flush()
        lead.current_listing_observation_id = observation.id
    profile = CustomerProfile(
        user_id=user_id,
        email=f"customer-{suffix}@example.com",
        licensed_states=["PA"],
        agent=customer,
        mapping_confirmed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    event = DistributionEvent(
        lead_id=lead.id,
        customer_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        listing_provenance={"kind": "legacy", "source": "manifest"},
    )
    session.add_all([profile, event])
    session.flush()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    filed = TestClient(app).post(
        "/api/me/feedback",
        json={
            "distribution_event_id": event.id,
            "disposition": disposition,
            "note": "The line was disconnected",
        },
    )
    assert filed.status_code == 201, filed.text
    session.expire_all()
    report = session.scalar(
        select(LeadReport).where(LeadReport.customer_id == customer.id)
    )
    return customer, lead, event, report, profile, user_id


def history(session) -> dict:
    """The records a resolution must never rewrite."""
    return {
        "distributions": [
            (
                item.id,
                item.lead_id,
                item.agent_id,
                item.title,
                item.state,
                item.phone,
                item.listing_provenance,
            )
            for item in session.scalars(
                select(DistributionEvent).order_by(DistributionEvent.id)
            )
        ],
        "observations": [
            (item.id, item.lead_id, item.title, item.state, item.valid)
            for item in session.scalars(
                select(ListingObservation).order_by(ListingObservation.id)
            )
        ],
        "reports": [
            (str(item.id), item.reason, item.details, item.created_at)
            for item in session.scalars(
                select(LeadReport).order_by(LeadReport.created_at)
            )
        ],
    }


def audit_actions(session) -> list[str]:
    return [
        entry.action
        for entry in session.scalars(
            select(AuditEntry).order_by(AuditEntry.created_at, AuditEntry.id)
        )
    ]


class TestReportQueueAndDetail:
    def test_the_queue_and_detail_carry_report_customer_event_and_controls(
        self,
        session,
    ):
        customer, lead, event, report, _, _ = reported_lead(
            session,
            with_observation=True,
        )
        client = as_admin(session)
        try:
            detail = client.get(f"/api/admin/lead-reports/{report.id}")
            assert detail.status_code == 200
            item = detail.json()
            assert item["details"] == "The line was disconnected"
            assert item["reasonLabel"] == "Invalid phone"
            assert item["customer"]["id"] == customer.id
            assert item["distributionEvent"]["id"] == event.id
            assert item["distributionEvent"]["title"] == "Delivered Title"
            assert item["lead"]["id"] == lead.id
            # Evidence is resolved live so the override can be compared.
            assert item["evidence"]["kind"] == "current_listing"
            assert item["evidence"]["title"] == "Observed Title"
            assert item["controls"]["eligibilityHeld"] is True
            assert item["controls"]["holdReleasableByCustomer"] is False
            assert "guarantee" in item["controls"]["restoreNotice"]
            names = [action["name"] for action in item["actions"]]
            assert names == ["dismiss", "correct", "suppress"]
        finally:
            app.dependency_overrides.clear()

    def test_administration_is_refused_without_an_administrator(self, session):
        _, _, _, report, _, _ = reported_lead(session)

        def database_override():
            yield session

        app.dependency_overrides[get_db] = database_override
        try:
            client = TestClient(app)
            assert client.get(
                f"/api/admin/lead-reports/{report.id}"
            ).status_code in {401, 403}
            assert client.post(
                f"/api/admin/lead-reports/{report.id}/dismiss",
                json={"note": "Not allowed"},
            ).status_code in {401, 403}
        finally:
            app.dependency_overrides.clear()

    def test_an_unknown_report_is_not_found(self, session):
        client = as_admin(session)
        try:
            missing = client.get(f"/api/admin/lead-reports/{uuid.uuid4()}")
            assert missing.status_code == 404
        finally:
            app.dependency_overrides.clear()


class TestDistinctEffects:
    def test_dismissal_releases_the_hold_and_leaves_the_lead_alone(
        self,
        session,
    ):
        _, lead, _, report, _, _ = reported_lead(session)
        before = history(session)
        client = as_admin(session)
        try:
            response = client.post(
                f"/api/admin/lead-reports/{report.id}/dismiss",
                json={"note": "Phone verified working"},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "dismissed"
            session.expire_all()
            assert session.get(LeadReport, report.id).status == "dismissed"
            assert session.get(Lead, lead.id).suppressed is False
            assert session.get(Lead, lead.id).active_correction_id is None
            hold = session.scalar(select(EligibilityHold))
            assert hold.active is False
            assert hold.release_reason == "Phone verified working"
            assert audit_actions(session) == ["lead_report_dismissed"]
            assert history(session) == before

            repeated = client.post(
                f"/api/admin/lead-reports/{report.id}/dismiss",
                json={"note": "Again"},
            )
            assert repeated.status_code == 409
        finally:
            app.dependency_overrides.clear()

    def test_correction_records_the_evidence_it_overrode(self, session):
        _, lead, _, report, _, _ = reported_lead(
            session,
            disposition="wrong_business",
            with_observation=True,
        )
        before = history(session)
        client = as_admin(session)
        try:
            response = client.post(
                f"/api/admin/lead-reports/{report.id}/correct",
                json={
                    "note": "Confirmed with the business directly",
                    "title": "Corrected Business",
                    "state": "NJ",
                },
            )
            assert response.status_code == 200
            session.expire_all()
            fresh = session.get(Lead, lead.id)
            assert fresh.title == "Corrected Business"
            assert fresh.state == "NJ"
            assert fresh.suppressed is False
            correction = session.scalar(select(LeadCorrectionEvent))
            assert correction.based_on_kind == "current_listing"
            assert correction.based_on_title == "Observed Title"
            assert correction.based_on_observation_id is not None
            # Correcting is its own consequential act, recorded as one.
            assert audit_actions(session) == [
                "lead_report_corrected",
                "lead_correction_applied",
            ]
            applied = session.scalar(
                select(AuditEntry).where(
                    AuditEntry.action == "lead_correction_applied"
                )
            )
            assert applied.details["evidence"]["kind"] == "current_listing"
            assert applied.details["evidence"]["title"] == "Observed Title"
            assert history(session) == before
        finally:
            app.dependency_overrides.clear()

    def test_correction_without_a_proposed_override_is_refused(self, session):
        _, _, _, report, _, _ = reported_lead(
            session,
            disposition="wrong_business",
        )
        client = as_admin(session)
        try:
            refused = client.post(
                f"/api/admin/lead-reports/{report.id}/correct",
                json={"note": "Nothing proposed"},
            )
            assert refused.status_code == 422
            session.expire_all()
            assert session.get(LeadReport, report.id).status == "open"
            assert session.scalar(select(func.count(AuditEntry.id))) == 0
        finally:
            app.dependency_overrides.clear()

    def test_suppression_blocks_allocation_and_states_the_restore_rule(
        self,
        session,
    ):
        _, lead, _, report, _, _ = reported_lead(session)
        before = history(session)
        client = as_admin(session)
        try:
            response = client.post(
                f"/api/admin/lead-reports/{report.id}/suppress",
                json={"note": "Do-not-contact request"},
            )
            assert response.status_code == 200
            assert "guarantee" in response.json()["restoreNotice"]
            session.expire_all()
            fresh = session.get(Lead, lead.id)
            assert fresh.suppressed is True
            assert fresh.suppression_reason == "Do-not-contact request"
            # Suppression is not a correction.
            assert fresh.active_correction_id is None
            assert audit_actions(session) == [
                "lead_report_suppressed",
                "lead_suppressed",
            ]
            assert history(session) == before
        finally:
            app.dependency_overrides.clear()


class TestHoldCannotBeBypassed:
    def test_a_customer_correction_never_releases_the_hold(self, session):
        _, lead, event, report, profile, user_id = reported_lead(session)
        # A different Customer, so permanent no-repeat history is not what is
        # being measured here -- only the Eligibility Hold.
        other = Agent(slug=f"other-{uuid.uuid4().hex[:8]}", name="Other Customer")
        session.add(other)
        session.flush()
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent_id=other.id,
            lead_count=1,
            states_snapshot=["PA"],
            state_mode="all_saved",
            delivery_email="other@example.com",
            status=RequestStatus.approved.value,
        )
        session.add(request)
        session.flush()
        settings = Settings()
        assert inventory_count(session, request, settings) == 0

        # The Customer walks their own report back. Nothing about eligibility
        # is theirs to decide.
        def database_override():
            yield session

        app.dependency_overrides[get_db] = database_override
        app.dependency_overrides[require_principal] = lambda: Principal(
            user_id=user_id,
            email=profile.email,
            role="customer",
            csrf="test",
        )
        try:
            walked_back = TestClient(app).post(
                "/api/me/feedback",
                json={
                    "distribution_event_id": event.id,
                    "disposition": "positive_response",
                    "note": "Reached them after all",
                },
            )
            assert walked_back.status_code == 201
            session.expire_all()
            hold = session.scalar(select(EligibilityHold))
            assert hold.active is True
            assert inventory_count(session, request, settings) == 0
            assert session.get(LeadReport, report.id).status == "open"
        finally:
            app.dependency_overrides.clear()

        client = as_admin(session)
        try:
            resolved = client.post(
                f"/api/admin/lead-reports/{report.id}/dismiss",
                json={"note": "Customer reached the business"},
            )
            assert resolved.status_code == 200
            session.expire_all()
            assert session.scalar(select(EligibilityHold)).active is False
            assert inventory_count(session, request, settings) == 1
        finally:
            app.dependency_overrides.clear()

    def test_a_customer_cannot_resolve_a_report_through_the_admin_surface(
        self,
        session,
    ):
        _, _, _, report, profile, user_id = reported_lead(session)

        def database_override():
            yield session

        app.dependency_overrides[get_db] = database_override
        app.dependency_overrides[require_principal] = lambda: Principal(
            user_id=user_id,
            email=profile.email,
            role="customer",
            csrf="test",
        )
        try:
            refused = TestClient(app).post(
                f"/api/admin/lead-reports/{report.id}/dismiss",
                json={"note": "Let me out"},
            )
            assert refused.status_code == 403
            session.expire_all()
            assert session.scalar(select(EligibilityHold)).active is True
        finally:
            app.dependency_overrides.clear()


class TestSuppressionAndCorrectionControls:
    def test_suppression_and_restoration_both_require_a_reason(self, session):
        _, lead, _, _, _, _ = reported_lead(session)
        client = as_admin(session)
        try:
            assert client.put(
                f"/api/admin/leads/{lead.id}/suppression",
                json={"reason": ""},
            ).status_code == 422
            assert client.request(
                "DELETE",
                f"/api/admin/leads/{lead.id}/suppression",
                json={},
            ).status_code == 422

            suppressed = client.put(
                f"/api/admin/leads/{lead.id}/suppression",
                json={"reason": "Legal request"},
            )
            assert suppressed.status_code == 200
            restored = client.request(
                "DELETE",
                f"/api/admin/leads/{lead.id}/suppression",
                json={"reason": "Request withdrawn"},
            )
            assert restored.status_code == 200
            assert "guarantee" in restored.json()["restoreNotice"]
            session.expire_all()
            assert session.get(Lead, lead.id).suppressed is False
        finally:
            app.dependency_overrides.clear()

    def test_removing_a_correction_falls_back_to_the_current_listing(
        self,
        session,
    ):
        _, lead, _, _, _, _ = reported_lead(session, with_observation=True)
        before = history(session)
        client = as_admin(session)
        try:
            applied = client.put(
                f"/api/admin/leads/{lead.id}/correction",
                json={
                    "title": "Manual Override",
                    "state": "NJ",
                    "reason": "Verified by phone",
                },
            )
            assert applied.status_code == 200
            assert applied.json()["evidence"]["kind"] == "current_listing"

            removed = client.request(
                "DELETE",
                f"/api/admin/leads/{lead.id}/correction",
                json={"reason": "Override no longer applies"},
            )
            assert removed.status_code == 200
            session.expire_all()
            fresh = session.get(Lead, lead.id)
            assert fresh.active_correction_id is None
            # Falls back to the observation, not to the legacy snapshot.
            assert fresh.title == "Observed Title"
            assert fresh.state == "PA"
            assert history(session) == before
        finally:
            app.dependency_overrides.clear()

    def test_the_fulfillment_workspace_surfaces_reports_and_holds(
        self,
        session,
    ):
        _, lead, _, report, _, _ = reported_lead(session)
        client = as_admin(session)
        try:
            workspace = client.get("/api/admin/fulfillment").json()
            assert [item["id"] for item in workspace["leadReports"]] == [
                str(report.id)
            ]
            assert [item["leadId"] for item in workspace["eligibilityHolds"]] == [
                lead.id
            ]
            assert workspace["leadReports"][0]["eligibilityHeld"] is True
        finally:
            app.dependency_overrides.clear()
