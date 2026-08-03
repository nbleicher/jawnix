from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from jawnix.allocation import allocate_request
from jawnix.api import _customer_invitation_redirect, app
from jawnix.customer_accounts import accept_user_account_invitation
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    AuditEntry,
    Agent,
    BatchArtifact,
    CustomerProfile,
    CustomerTombstone,
    DistributionEvent,
    Job,
    Lead,
    LeadDispositionState,
    LeadDispositionTransition,
    LeadOutcome,
    LeadCorrectionEvent,
    LeadReport,
    LeadRequest,
    NightlyReview,
    ScraperConfiguration,
    ScrapeAnomaly,
    ScraperRun,
    SourceSegment,
    SourceRecommendation,
    UserAccount,
    UserAccountInvitation,
    utcnow,
)
from jawnix.telegram import anomaly_callback_data, callback_data
from jawnix.performance import build_source_recommendations


def test_admin_reconciles_unknown_nightly_delivery_without_duplicate_send(
    session,
):
    run = ScraperRun(
        source="google_maps",
        source_version="unknown-delivery",
        status="complete",
        details={},
    )
    session.add(run)
    session.flush()
    review = NightlyReview(
        scraper_run_id=run.id,
        summary={"run": {"id": run.id}},
        telegram_delivery_state="unknown",
        telegram_delivery_error="Worker interrupted",
    )
    session.add(review)
    session.commit()

    def database_override():
        yield session

    admin_id = uuid.uuid4()
    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        response = TestClient(app).post(
            (
                f"/api/admin/nightly-reviews/{review.id}/"
                "telegram-delivery/reconcile"
            ),
            json={
                "outcome": "not_delivered",
                "reason": "Confirmed no message appeared in Telegram",
            },
        )
        assert response.status_code == 200
        assert response.json()["telegramDeliveryState"] == "pending"
        session.refresh(review)
        assert review.telegram_delivery_state == "pending"
        assert session.scalar(
            select(func.count(Job.id)).where(
                Job.kind == "notify_nightly_review",
                Job.payload["review_id"].as_string() == str(review.id),
            )
        ) == 1
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action
                == "nightly_review_telegram_not_delivered"
            )
        )
        assert audit is not None
        assert audit.actor_user_id == str(admin_id)
    finally:
        app.dependency_overrides.clear()


def test_admin_can_inspect_unknown_nightly_review_delivery(session):
    run = ScraperRun(
        source="google_maps",
        source_version="inspect-unknown-delivery",
        status="complete",
        details={},
    )
    session.add(run)
    session.flush()
    review = NightlyReview(
        scraper_run_id=run.id,
        summary={"run": {"id": run.id, "status": "complete"}},
        telegram_delivery_state="unknown",
        telegram_delivery_error="Worker interrupted after dispatch",
    )
    session.add(review)
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        response = TestClient(app).get(
            "/api/admin/nightly-reviews",
            params={"telegram_delivery_state": "unknown"},
        )
        assert response.status_code == 200
        assert response.json() == [
            {
                "id": str(review.id),
                "scraperRunId": run.id,
                "status": "complete",
                "summary": review.summary,
                "telegramDeliveryState": "unknown",
                "telegramMessageId": "",
                "telegramDeliveryError": (
                    "Worker interrupted after dispatch"
                ),
                "telegramDeliveryStartedAt": None,
                "createdAt": review.created_at.isoformat(),
            }
        ]
    finally:
        app.dependency_overrides.clear()


def test_admin_cannot_reconcile_an_inflight_nightly_delivery(session):
    run = ScraperRun(
        source="google_maps",
        source_version="inflight-delivery",
        status="complete",
        details={},
    )
    session.add(run)
    session.flush()
    review = NightlyReview(
        scraper_run_id=run.id,
        summary={"run": {"id": run.id}},
        telegram_delivery_state="sending",
        telegram_delivery_started_at=utcnow(),
    )
    session.add(review)
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        response = TestClient(app).post(
            (
                f"/api/admin/nightly-reviews/{review.id}/"
                "telegram-delivery/reconcile"
            ),
            json={
                "outcome": "not_delivered",
                "reason": "Attempted too early",
            },
        )
        assert response.status_code == 409
        session.refresh(review)
        assert review.telegram_delivery_state == "sending"
        assert not list(
            session.scalars(
                select(Job).where(
                    Job.kind == "notify_nightly_review"
                )
            )
        )
    finally:
        app.dependency_overrides.clear()


def test_admin_confirms_unknown_nightly_review_was_delivered(session):
    run = ScraperRun(
        source="google_maps",
        source_version="confirm-delivery",
        status="held_anomaly",
        details={},
    )
    session.add(run)
    session.flush()
    anomaly = ScrapeAnomaly(
        scraper_run_id=run.id,
        configuration_id=uuid.uuid4(),
        dataset_checksum="a" * 64,
    )
    review = NightlyReview(
        scraper_run_id=run.id,
        summary={"run": {"id": run.id}},
        telegram_delivery_state="unknown",
    )
    session.add_all([anomaly, review])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        response = TestClient(app).post(
            (
                f"/api/admin/nightly-reviews/{review.id}/"
                "telegram-delivery/reconcile"
            ),
            json={
                "outcome": "delivered",
                "message_id": "9876",
                "reason": "Located the accepted message in Telegram",
            },
        )
        assert response.status_code == 200
        assert response.json()["telegramDeliveryState"] == "sent"
        session.refresh(review)
        session.refresh(anomaly)
        assert review.telegram_message_id == "9876"
        assert anomaly.telegram_message_id == "9876"
        assert not list(
            session.scalars(
                select(Job).where(
                    Job.kind == "notify_nightly_review"
                )
            )
        )
    finally:
        app.dependency_overrides.clear()


def test_request_mapping_state_validation_cancel_and_billing_404(session):
    user_id = uuid.uuid4()
    agent = Agent(slug="api-agent", name="API Agent")
    profile = CustomerProfile(
        user_id=user_id,
        email="customer@example.com",
        licensed_states=["TX", "FL"],
    )
    session.add_all([agent, profile])
    session.commit()

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
        client = TestClient(app)
        assert client.post("/api/generate-invoice", json={}).status_code == 404
        assert client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "all_saved", "states": []},
        ).status_code == 409

        profile.agent_id = agent.id
        profile.mapping_confirmed_at = utcnow()
        session.commit()
        session.expire(profile, ["agent"])
        outside = client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["PA"]},
        )
        assert outside.status_code == 422

        created = client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["TX"]},
        )
        assert created.status_code == 201
        request_id = created.json()["id"]
        assert created.json()["states_snapshot"] == ["TX"]
        assert session.scalar(select(func.count(Job.id)).where(Job.kind == "notify_request")) == 1
        assert client.delete(f"/api/me/requests/{request_id}").status_code == 200
        assert session.get(LeadRequest, uuid.UUID(request_id)).status == "canceled"
        assert client.delete(f"/api/me/requests/{request_id}").status_code == 409

        approved = client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["TX"]},
        )
        approved_id = uuid.UUID(approved.json()["id"])
        session.get(LeadRequest, approved_id).status = "approved"
        session.commit()
        assert client.delete(f"/api/me/requests/{approved_id}").status_code == 200

        agent.active = False
        session.commit()
        assert client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["TX"]},
        ).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_general_profile_patch_refuses_to_bypass_licensed_state_impact_review(session):
    user_id = uuid.uuid4()
    customer = Agent(slug="licensed-customer", name="Licensed Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="licensed@example.com",
        first_name="Licensed",
        last_name="Customer",
        licensed_states=["TX", "FL"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    narrowed = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=10,
        state_mode="all_saved",
        states_snapshot=["TX", "FL"],
        delivery_email=profile.email,
        status="approved",
        approved_at=utcnow(),
    )
    canceled = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=5,
        state_mode="selected",
        states_snapshot=["TX"],
        delivery_email=profile.email,
        status="waiting_inventory",
        approved_at=utcnow(),
    )
    committed = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=1,
        state_mode="all_saved",
        states_snapshot=["TX", "FL"],
        delivery_email=profile.email,
        status="generated",
        approved_at=utcnow(),
        processed_at=utcnow(),
    )
    session.add_all([customer, profile, narrowed, canceled, committed])
    session.commit()

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
        response = TestClient(app).patch(
            "/api/me/profile",
            json={
                "first_name": profile.first_name,
                "last_name": profile.last_name,
                "phone": "",
                "licensed_states": ["FL", "CA"],
            },
        )
        assert response.status_code == 409
        assert response.json() == {
            "detail": (
                "Licensed State changes require an impact review. "
                "Use Account to review and confirm them."
            )
        }

        session.refresh(narrowed)
        session.refresh(canceled)
        session.refresh(committed)
        assert narrowed.states_snapshot == ["TX", "FL"]
        assert narrowed.status == "approved"
        assert narrowed.approved_at is not None
        assert canceled.states_snapshot == ["TX"]
        assert canceled.status == "waiting_inventory"
        assert committed.states_snapshot == ["TX", "FL"]

        updates = list(
            session.scalars(
                select(Job)
                .where(Job.kind == "licensed_states_changed")
                .order_by(Job.id)
            )
        )
        assert updates == []
    finally:
        app.dependency_overrides.clear()


def test_customer_looks_up_most_recent_delivered_lead_by_phone_without_inventory_disclosure(
    session,
):
    user_id = uuid.uuid4()
    customer = Agent(slug="lookup-customer", name="Lookup Customer")
    other_customer = Agent(
        slug="other-lookup-customer",
        name="Other Lookup Customer",
    )
    profile = CustomerProfile(
        user_id=user_id,
        email="lookup@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(
        phone="2145555001",
        title="Most Recent Roofing",
        state="TX",
    )
    other_lead = Lead(
        phone="2145555002",
        title="Private Plumbing",
        state="TX",
    )
    session.add_all(
        [customer, other_customer, profile, lead, other_lead]
    )
    session.flush()
    batch = LeadRequest(
        user_id=user_id,
        agent_id=customer.id,
        lead_count=1,
        state_mode="all_saved",
        states_snapshot=["TX"],
        delivery_email=profile.email,
        status="delivered",
    )
    session.add(batch)
    session.flush()
    older = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title="Older Roofing",
        state=lead.state,
        delivered_at=utcnow() - timedelta(days=2),
        source="older-batch",
    )
    newest = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        delivered_at=utcnow() - timedelta(days=1),
        source="newer-batch",
        request_id=batch.id,
    )
    private = DistributionEvent(
        lead_id=other_lead.id,
        agent_id=other_customer.id,
        customer_name=other_customer.name,
        phone=other_lead.phone,
        title=other_lead.title,
        state=other_lead.state,
        delivered_at=utcnow(),
        source="private-batch",
    )
    session.add_all([older, newest, private])
    session.commit()

    def database_override():
        yield session

    principal = Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: principal
    try:
        client = TestClient(app)
        found = client.post(
            "/api/me/feedback/lookup",
            json={"phone": "+1 (214) 555-5001"},
        )
        assert found.status_code == 200
        assert found.json() == {
            "distributionEventId": newest.id,
            "businessName": "Most Recent Roofing",
            "phone": "2145555001",
            "deliveredAt": newest.delivered_at.isoformat(),
            "batchId": str(batch.id),
            "currentDisposition": None,
        }

        private_lookup = client.post(
            "/api/me/feedback/lookup",
            json={"phone": "2145555002"},
        )
        missing_lookup = client.post(
            "/api/me/feedback/lookup",
            json={"phone": "2145555999"},
        )
        invalid_lookup = client.post(
            "/api/me/feedback/lookup",
            json={"phone": "123"},
        )
        empty_lookup = client.post(
            "/api/me/feedback/lookup",
            json={"phone": ""},
        )
        overlong_lookup = client.post(
            "/api/me/feedback/lookup",
            json={"phone": "1" * 100},
        )
        for response in (
            private_lookup,
            missing_lookup,
            invalid_lookup,
            empty_lookup,
            overlong_lookup,
        ):
            assert response.status_code == 404
            assert response.json() == {
                "detail": "No delivered Lead found."
            }
    finally:
        app.dependency_overrides.clear()


def test_customer_feedback_preserves_milestones_and_materializes_current_state(
    session,
):
    user_id = uuid.uuid4()
    customer = Agent(
        slug="disposition-customer",
        name="Disposition Customer",
    )
    profile = CustomerProfile(
        user_id=user_id,
        email="disposition@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(
        phone="2145555010",
        title="Disposition Roofing",
        state="TX",
    )
    session.add_all([customer, profile, lead])
    session.flush()
    delivered = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        delivered_at=utcnow(),
        source_segment_key="roofing|TX",
    )
    session.add(delivered)
    session.commit()

    def database_override():
        yield session

    principal = Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: principal
    try:
        client = TestClient(app)
        positive = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": delivered.id,
                "disposition": "positive_response",
            },
        )
        assert positive.status_code == 201
        assert positive.json()["transition"]["actorUserId"] == str(
            user_id
        )

        booked = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": delivered.id,
                "disposition": "appointment_booked",
            },
        )
        assert booked.status_code == 201
        assert booked.json()["currentDisposition"] == (
            "appointment_booked"
        )
        assert booked.json()["transition"]["previousTransitionId"] == (
            positive.json()["transition"]["id"]
        )

        canceled = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": delivered.id,
                "disposition": "appointment_canceled",
                "note": "Prospect rescheduled elsewhere",
            },
        )
        assert canceled.status_code == 201
        assert canceled.json()["currentDisposition"] == (
            "appointment_canceled"
        )
        assert canceled.json()["transition"]["previousTransitionId"] == (
            booked.json()["transition"]["id"]
        )

        no_show = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": delivered.id,
                "disposition": "appointment_no_show",
            },
        )
        assert no_show.status_code == 201
        assert no_show.json()["currentDisposition"] == (
            "appointment_no_show"
        )
        assert no_show.json()["transition"]["previousTransitionId"] == (
            canceled.json()["transition"]["id"]
        )

        history = client.get(
            f"/api/me/distributions/{delivered.id}/dispositions"
        )
        assert history.status_code == 200
        assert [
            item["disposition"] for item in history.json()
        ] == [
            "positive_response",
            "appointment_booked",
            "appointment_canceled",
            "appointment_no_show",
        ]
        assert history.json()[2]["note"] == (
            "Prospect rescheduled elsewhere"
        )
        assert {
            item["actorUserId"] for item in history.json()
        } == {str(user_id)}

        persisted = list(
            session.scalars(
                select(LeadDispositionTransition)
                .where(
                    LeadDispositionTransition.distribution_event_id
                    == delivered.id
                )
                .order_by(
                    LeadDispositionTransition.created_at,
                    LeadDispositionTransition.id,
                )
            )
        )
        assert {
            item.customer_id for item in persisted
        } == {customer.id}
        assert {
            item.actor_user_id for item in persisted
        } == {user_id}
        assert {
            item.distribution_event_id for item in persisted
        } == {delivered.id}
        attributed_event = session.get(
            DistributionEvent,
            persisted[-1].distribution_event_id,
        )
        assert attributed_event.source_segment_key == "roofing|TX"
        current = session.get(LeadDispositionState, delivered.id)
        assert current.current_transition_id == persisted[-1].id
        assert current.current_disposition == "appointment_no_show"

        lookup = client.post(
            "/api/me/feedback/lookup",
            json={"phone": lead.phone},
        )
        assert lookup.status_code == 200
        assert lookup.json()["currentDisposition"] == (
            "appointment_no_show"
        )
    finally:
        app.dependency_overrides.clear()


def test_customer_feedback_accepts_optional_quality_rating_and_requires_other_note(
    session,
):
    user_id = uuid.uuid4()
    customer = Agent(
        slug="rating-customer",
        name="Rating Customer",
    )
    profile = CustomerProfile(
        user_id=user_id,
        email="rating@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(
        phone="2145555011",
        title="Rating Roofing",
        state="TX",
    )
    session.add_all([customer, profile, lead])
    session.flush()
    delivered = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        delivered_at=utcnow(),
    )
    session.add(delivered)
    session.commit()

    def database_override():
        yield session

    principal = Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: principal
    try:
        client = TestClient(app)
        missing_note = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": delivered.id,
                "disposition": "other",
            },
        )
        assert missing_note.status_code == 422

        first = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": delivered.id,
                "disposition": "other",
                "note": "Asked to call a different department",
                "quality_rating": "good",
            },
        )
        assert first.status_code == 201
        assert first.json()["qualityRating"]["kind"] == "good"
        assert (
            first.json()["qualityRating"]["supersedesOutcomeId"]
            is None
        )

        corrected = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": delivered.id,
                "disposition": "not_interested",
                "quality_rating": "poor",
                "quality_note": "Business details were incomplete",
            },
        )
        assert corrected.status_code == 201
        assert corrected.json()["qualityRating"]["kind"] == "poor"
        assert corrected.json()["qualityRating"][
            "supersedesOutcomeId"
        ] == first.json()["qualityRating"]["id"]

        rating_history = client.get(
            f"/api/me/distributions/{delivered.id}/outcomes"
        )
        assert rating_history.status_code == 200
        assert [
            item["kind"] for item in rating_history.json()
        ] == ["good", "poor"]
        assert rating_history.json()[1]["note"] == (
            "Business details were incomplete"
        )
    finally:
        app.dependency_overrides.clear()


def test_customer_feedback_accepts_controlled_dispositions_without_disclosing_other_deliveries(
    session,
):
    user_id = uuid.uuid4()
    customer = Agent(
        slug="controlled-disposition-customer",
        name="Controlled Disposition Customer",
    )
    other_customer = Agent(
        slug="private-disposition-customer",
        name="Private Disposition Customer",
    )
    profile = CustomerProfile(
        user_id=user_id,
        email="controlled-disposition@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(
        phone="2145555012",
        title="Controlled Roofing",
        state="TX",
    )
    private_lead = Lead(
        phone="2145555013",
        title="Private Roofing",
        state="TX",
    )
    session.add_all(
        [customer, other_customer, profile, lead, private_lead]
    )
    session.flush()
    delivered = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        delivered_at=utcnow(),
    )
    private = DistributionEvent(
        lead_id=private_lead.id,
        agent_id=other_customer.id,
        customer_name=other_customer.name,
        phone=private_lead.phone,
        title=private_lead.title,
        state=private_lead.state,
        delivered_at=utcnow(),
    )
    session.add_all([delivered, private])
    session.commit()

    def database_override():
        yield session

    principal = Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: principal
    try:
        client = TestClient(app)
        dispositions = [
            "no_contact",
            "not_interested",
            "positive_response",
            "appointment_booked",
            "appointment_canceled",
            "appointment_no_show",
            "invalid_phone",
            "wrong_business",
            "do_not_contact",
        ]
        for disposition in dispositions:
            response = client.post(
                "/api/me/feedback",
                json={
                    "distribution_event_id": delivered.id,
                    "disposition": disposition,
                },
            )
            assert response.status_code == 201, disposition
        assert client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": delivered.id,
                "disposition": "other",
                "note": "Customer supplied context",
            },
        ).status_code == 201

        private_submission = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": private.id,
                "disposition": "positive_response",
            },
        )
        missing_submission = client.post(
            "/api/me/feedback",
            json={
                "distribution_event_id": 999_999,
                "disposition": "positive_response",
            },
        )
        for response in (private_submission, missing_submission):
            assert response.status_code == 404
            assert response.json() == {
                "detail": "No delivered Lead found."
            }
    finally:
        app.dependency_overrides.clear()


def test_late_feedback_keeps_correction_history_and_performance_denominators(
    session,
):
    user_id = uuid.uuid4()
    customer = Agent(slug="feedback-customer", name="Feedback Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="feedback@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(phone="2145555001", title="Roofing One", state="TX")
    legacy = Lead(
        phone="2145555002",
        title="Legacy",
        state="TX",
        source_flow="nppes",
    )
    session.add_all([customer, profile, lead, legacy])
    session.flush()
    delivered = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        source_kind="google_maps",
        source_segment_key="roofing|TX|maps",
        source_niche="roofing",
        delivered_at=utcnow(),
    )
    legacy_delivered = DistributionEvent(
        lead_id=legacy.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=legacy.phone,
        title=legacy.title,
        state=legacy.state,
        source_kind="legacy",
        delivered_at=utcnow(),
    )
    session.add_all([delivered, legacy_delivered])
    session.commit()

    def database_override():
        yield session

    principal = Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: principal
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        poor = client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={"kind": "poor"},
        )
        assert poor.status_code == 201
        positive = client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={"kind": "positive_response"},
        )
        assert positive.status_code == 201
        assert client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={"kind": "positive_response"},
        ).status_code == 409
        assert client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={"kind": "appointment_booked"},
        ).status_code == 422

        corrected = client.post(
            f"/api/me/distributions/{delivered.id}/outcomes",
            json={
                "kind": "good",
                "supersedes_outcome_id": poor.json()["id"],
            },
        )
        assert corrected.status_code == 201
        history = client.get(
            f"/api/me/distributions/{delivered.id}/outcomes"
        )
        assert history.status_code == 200
        assert [item["kind"] for item in history.json()] == [
            "poor",
            "positive_response",
            "good",
        ]

        performance = client.get("/api/admin/source-performance")
        assert performance.status_code == 200
        body = performance.json()
        assert body["cohorts"] == body["segments"]
        assert body["cohorts"] == [
            {
                "segment": "roofing|TX|maps",
                "niche": "roofing",
                "distributionPeriod": delivered.delivered_at.strftime(
                    "%Y-%m"
                ),
                "delivered": 1,
                "worked": 1,
                "rated": 1,
                "good": 1,
                "poor": 0,
                "positiveResponses": 1,
                "appointmentsBooked": 0,
                "goodRate": 1.0,
                "positiveResponseRate": 1.0,
                "appointmentRate": 0.0,
                "qualityStatus": "insufficient_data",
                "conversionStatus": "insufficient_data",
            }
        ]
        assert body["global"]["prescriptive"] is False
        assert body["global"]["worked"] == 1
        assert body["global"]["rates"] == {
            "good": 1.0,
            "positiveResponse": 1.0,
            "appointmentBooked": 0.0,
        }
        assert body["legacy"] == {
            "delivered": 1,
            "excludedFromRecommendations": True,
        }
        assert session.scalar(select(func.count(LeadOutcome.id))) == 3
    finally:
        app.dependency_overrides.clear()


def test_admin_can_send_recipient_password_reset(session, settings, monkeypatch):
    user_id = uuid.uuid4()
    profile = CustomerProfile(
        user_id=user_id,
        email="activation@example.com",
        licensed_states=[],
    )
    session.add(profile)
    session.commit()
    sent: list[str] = []

    async def fake_send(_settings, email):
        sent.append(email)

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    monkeypatch.setattr("jawnix.api._send_password_reset", fake_send)
    try:
        client = TestClient(app)
        response = client.post(f"/api/admin/recipients/{user_id}/send-password-reset")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "email": profile.email}
        assert sent == [profile.email]
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action
                == "user_account_password_reset_sent"
            )
        )
        assert audit is not None
        assert audit.target_id == str(user_id)
        assert audit.details["after"] == {"resetDispatched": True}
        assert client.post(f"/api/admin/recipients/{uuid.uuid4()}/send-password-reset").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_customer_invitation_redirect_targets_the_shell(settings):
    settings = settings.model_copy(
        update={"public_base_url": "https://jawnix.example/"}
    )

    assert (
        _customer_invitation_redirect(settings)
        == "https://jawnix.example/app/accept-invitation"
    )


def test_admin_batch_request_decision_records_actor_reason_and_change(
    session,
):
    admin_id = uuid.uuid4()
    customer = Agent(
        slug="request-audit-customer",
        name="Request Audit Customer",
    )
    user_id = uuid.uuid4()
    profile = CustomerProfile(
        user_id=user_id,
        email="request-audit@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    request = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=10,
        state_mode="selected",
        states_snapshot=["TX"],
        delivery_email="request-audit@example.com",
        status="pending",
    )
    session.add_all([customer, profile, request])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        response = TestClient(app).post(
            f"/api/admin/requests/{request.id}/approve",
            json={"reason": "Inventory and Customer scope verified."},
        )
        assert response.status_code == 200
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "batch_request_approve"
            )
        )
        assert audit is not None
        assert audit.actor_user_id == str(admin_id)
        assert audit.target_type == "batch_request"
        assert audit.target_id == str(request.id)
        assert audit.reason == "Inventory and Customer scope verified."
        assert audit.details == {
            "before": {"status": "pending"},
            "after": {"status": "approved"},
        }
    finally:
        app.dependency_overrides.clear()


def test_admin_can_view_and_edit_agency_agent_hierarchy(session):
    first_agency = Agency(slug="first-agency", name="First Agency")
    second_agency = Agency(slug="second-agency", name="Second Agency", active=False)
    agent = Agent(slug="hierarchy-agent", name="Hierarchy Agent", agency=first_agency, active=False)
    cascade_agent = Agent(slug="cascade-agent", name="Cascade Agent", agency=first_agency)
    profile = CustomerProfile(
        user_id=uuid.uuid4(),
        email="hierarchy@example.com",
        licensed_states=["TX"],
        agent=agent,
        mapping_confirmed_at=utcnow(),
    )
    cascade_profile = CustomerProfile(
        user_id=uuid.uuid4(),
        email="cascade@example.com",
        licensed_states=["FL"],
        agent=cascade_agent,
        mapping_confirmed_at=utcnow(),
    )
    lead = Lead(phone="2125550100", title="Test", state="NY")
    session.add_all([first_agency, second_agency, agent, cascade_agent, profile, cascade_profile, lead])
    session.flush()
    event = DistributionEvent(lead_id=lead.id, agent_id=agent.id, source="test")
    session.add(event)
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        hierarchy = client.get("/api/admin/recipients")
        assert hierarchy.status_code == 200
        data = hierarchy.json()
        assert {item["slug"] for item in data["agencies"]} == {"first-agency", "second-agency"}
        returned_agent = next(item for item in data["agents"] if item["slug"] == agent.slug)
        assert returned_agent == {
            "id": agent.id,
            "slug": "hierarchy-agent",
            "name": "Hierarchy Agent",
            "active": False,
            "agencyId": first_agency.id,
            "agency": "First Agency",
        }
        customers = client.get("/api/admin/customers")
        assert customers.status_code == 200
        returned_customer = next(
            item
            for item in customers.json()["customers"]
            if item["slug"] == agent.slug
        )
        assert returned_customer["id"] == agent.id
        assert returned_customer["name"] == "Hierarchy Agent"
        assert "agents" not in customers.json()

        agency_update = client.patch(
            f"/api/admin/agencies/{second_agency.id}",
            json={"name": "Renamed Agency", "active": True},
        )
        assert agency_update.status_code == 200
        session.refresh(second_agency)
        assert second_agency.name == "Renamed Agency"
        assert second_agency.active is True

        bypassed_assignment = client.patch(
            f"/api/admin/agents/{agent.id}",
            json={"name": "Renamed Agent", "agency_id": second_agency.id, "active": True},
        )
        assert bypassed_assignment.status_code == 409
        agent_update = client.patch(
            f"/api/admin/agents/{agent.id}",
            json={
                "name": "Renamed Agent",
                "agency_id": first_agency.id,
                "active": True,
            },
        )
        assert agent_update.status_code == 200
        assignment = client.post(
            f"/api/admin/customers/{agent.id}/agency-assignment",
            json={
                "agency_id": second_agency.id,
                "reason": "Moved through the permanent-history workflow.",
                "confirmed": True,
            },
        )
        assert assignment.status_code == 200
        session.refresh(agent)
        assert agent.name == "Renamed Agent"
        assert agent.agency_id == second_agency.id
        assert agent.active is True

        assert client.patch(
            f"/api/admin/agents/{agent.id}",
            json={"name": "Agent", "agency_id": 999_999, "active": True},
        ).status_code == 409
        assert client.patch(
            f"/api/admin/agencies/{first_agency.id}",
            json={"name": " ", "active": True},
        ).status_code == 422
        assert client.patch(
            f"/api/admin/agencies/{first_agency.id}",
            json={"name": "Agency", "active": True, "slug": "cannot-change"},
        ).status_code == 422

        assert client.request(
            "DELETE",
            f"/api/admin/agents/{agent.id}",
            json={"confirm_slug": "wrong"},
        ).status_code == 409
        deleted_agent = client.request(
            "DELETE",
            f"/api/admin/agents/{agent.id}",
            json={"confirm_slug": agent.slug},
        )
        assert deleted_agent.status_code == 200
        assert deleted_agent.json() == {
            "ok": True,
            "unassignedRecipients": 1,
            "historyPreserved": True,
        }
        session.refresh(agent)
        session.refresh(profile)
        session.refresh(event)
        assert agent.active is False
        assert agent.deleted_at is not None
        assert profile.agent_id is None
        assert profile.mapping_confirmed_at is None
        assert event.agent_id == agent.id

        active_request = LeadRequest(
            user_id=cascade_profile.user_id,
            agent_id=cascade_agent.id,
            lead_count=10,
            state_mode="all_saved",
            states_snapshot=["FL"],
            delivery_email=cascade_profile.email,
            status="pending",
        )
        first_agency.active = False
        session.add(active_request)
        session.commit()
        active_customer_delete = client.request(
            "DELETE",
            f"/api/admin/agencies/{first_agency.id}",
            json={"confirm_slug": first_agency.slug},
        )
        assert active_customer_delete.status_code == 409
        assert active_customer_delete.json()["detail"] == {
            "message": (
                "Deactivate every Customer before deleting the Agency."
            ),
            "activeCustomers": [cascade_agent.slug],
        }
        cascade_agent.active = False
        session.commit()
        blocked_agency_delete = client.request(
            "DELETE",
            f"/api/admin/agencies/{first_agency.id}",
            json={"confirm_slug": first_agency.slug},
        )
        assert blocked_agency_delete.status_code == 409
        assert blocked_agency_delete.json()["detail"] == "Resolve 1 active request(s) before deleting this agency."
        active_request.status = "canceled"
        session.commit()

        deleted_agency = client.request(
            "DELETE",
            f"/api/admin/agencies/{first_agency.id}",
            json={"confirm_slug": first_agency.slug},
        )
        assert deleted_agency.status_code == 200
        assert deleted_agency.json() == {
            "ok": True,
            "deletedCustomers": 1,
            "unassignedRecipients": 1,
            "historyPreserved": True,
        }
        session.refresh(first_agency)
        session.refresh(cascade_agent)
        session.refresh(cascade_profile)
        assert first_agency.deleted_at is not None
        assert cascade_agent.deleted_at is not None
        assert cascade_profile.agent_id is None
        assert cascade_profile.mapping_confirmed_at is None

        remaining = client.get("/api/admin/recipients").json()
        assert "first-agency" not in {item["slug"] for item in remaining["agencies"]}
        assert "hierarchy-agent" not in {item["slug"] for item in remaining["agents"]}
        assert "cascade-agent" not in {item["slug"] for item in remaining["agents"]}
    finally:
        app.dependency_overrides.clear()


def test_replacement_waits_for_acceptance_then_swaps_access_atomically(
    session,
    settings,
    monkeypatch,
):
    """The prior account keeps working until the replacement is accepted."""
    old_user_id = uuid.uuid4()
    new_user_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    customer = Agent(
        slug="replaceable-account-customer",
        name="Replaceable Account Customer",
        licensed_states=["TX", "FL"],
    )
    old_profile = CustomerProfile(
        user_id=old_user_id,
        email="old-account@example.com",
        licensed_states=["TX", "FL"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    old_account = UserAccount(
        auth_user_id=old_user_id,
        email=old_profile.email,
        customer=customer,
        active=True,
    )
    session.add_all([customer, old_profile, old_account])
    session.commit()
    event = DistributionEvent(
        lead_id=None,
        agent_id=customer.id,
        customer_name=customer.name,
        phone="2145550111",
        title="Existing history",
        state="TX",
    )
    lead = Lead(phone="2145550111", title="Existing history", state="TX")
    session.add(lead)
    session.flush()
    event.lead_id = lead.id
    session.add(event)
    session.commit()
    history_event_id = event.id

    settings = settings.model_copy(
        update={
            "supabase_url": "https://auth.example.test",
            "supabase_service_role_key": "service-role-key",
            "public_base_url": "https://jawnix.example",
        }
    )

    async def fake_admin(_settings, _method, _path, _payload):
        return {"id": str(new_user_id)}

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    monkeypatch.setattr("jawnix.api._supabase_admin", fake_admin)
    try:
        client = TestClient(app)
        invited = client.post(
            f"/api/admin/customers/{customer.id}/user-account-invitation",
            json={"email": "new-account@example.com"},
        )
        assert invited.status_code == 201
        assert invited.json()["activated"] is False
        assert invited.json()["replacesAuthUserId"] == str(old_user_id)

        # Nothing about access has changed yet.
        session.refresh(old_account)
        assert old_account.active is True
        assert old_account.replaced_at is None
        assert session.get(UserAccount, new_user_id) is None

        details = client.get(
            f"/api/admin/customers/{customer.id}/details"
        ).json()
        assert details["user_account"]["auth_user_id"] == str(old_user_id)
        assert details["invitation"]["email"] == "new-account@example.com"
        assert details["history"]["distributions"] == 1

        # The prior account can still sign in while the invitation is open.
        app.dependency_overrides[require_principal] = lambda: Principal(
            user_id=old_user_id,
            email=old_profile.email,
            role="customer",
            csrf="test",
        )
        assert client.get("/api/me/profile").status_code == 200

        accepted = accept_user_account_invitation(
            session,
            auth_user_id=new_user_id,
            email="new-account@example.com",
        )
        session.commit()
        assert accepted is not None

        session.refresh(old_account)
        assert old_account.active is False
        assert old_account.replaced_at is not None
        assert old_account.replaced_by_auth_user_id == new_user_id
        new_account = session.get(UserAccount, new_user_id)
        assert new_account is not None and new_account.active is True
        assert new_account.customer_id == customer.id

        # The durable Customer and its history are untouched.
        session.refresh(customer)
        assert customer.id == new_account.customer_id
        assert customer.slug == "replaceable-account-customer"
        assert session.get(DistributionEvent, history_event_id).agent_id == (
            customer.id
        )
        assert session.scalar(
            select(func.count(DistributionEvent.id)).where(
                DistributionEvent.agent_id == customer.id
            )
        ) == 1

        assert client.get("/api/me/profile").status_code == 403
        app.dependency_overrides[require_principal] = lambda: Principal(
            user_id=new_user_id,
            email="new-account@example.com",
            role="customer",
            csrf="test",
        )
        current = client.get("/api/me/profile")
        assert current.status_code == 200
        assert current.json()["customer_id"] == customer.id
        assert current.json()["licensed_states"] == ["FL", "TX"]

        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "customer_user_account_replaced"
            )
        )
        assert audit is not None
        assert audit.target_type == "customer"
        assert audit.target_id == str(customer.id)
        assert audit.details["before"] == {
            "activeAuthUserIds": [str(old_user_id)]
        }
        assert audit.details["after"] == {
            "activeAuthUserIds": [str(new_user_id)]
        }
        assert audit.details["historyPreserved"] is True
    finally:
        app.dependency_overrides.clear()


def test_persistence_refuses_a_second_active_user_account_per_customer(
    session,
):
    """The one-active-account rule is a database constraint, not a screen rule."""
    customer = Agent(slug="constrained-customer", name="Constrained Customer")
    session.add(customer)
    session.flush()
    session.add(
        UserAccount(
            auth_user_id=uuid.uuid4(),
            email="first@example.com",
            customer_id=customer.id,
            active=True,
        )
    )
    session.commit()

    session.add(
        UserAccount(
            auth_user_id=uuid.uuid4(),
            email="second@example.com",
            customer_id=customer.id,
            active=True,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # And at most one outstanding invitation, for the same reason.
    session.add(
        UserAccountInvitation(
            customer_id=customer.id,
            auth_user_id=uuid.uuid4(),
            email="invited@example.com",
            status="pending",
            invited_by="admin",
            reason="First invitation",
        )
    )
    session.commit()
    session.add(
        UserAccountInvitation(
            customer_id=customer.id,
            auth_user_id=uuid.uuid4(),
            email="other@example.com",
            status="pending",
            invited_by="admin",
            reason="Second invitation",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_failed_invitation_dispatch_leaves_the_customer_untouched(
    session,
    settings,
    monkeypatch,
):
    old_user_id = uuid.uuid4()
    customer = Agent(slug="invite-failure-customer", name="Invite Failure")
    profile = CustomerProfile(
        user_id=old_user_id,
        email="held@example.com",
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    account = UserAccount(
        auth_user_id=old_user_id,
        email=profile.email,
        customer=customer,
        active=True,
    )
    session.add_all([customer, profile, account])
    session.commit()

    settings = settings.model_copy(
        update={
            "supabase_url": "https://auth.example.test",
            "supabase_service_role_key": "service-role-key",
        }
    )

    async def failing_admin(*_args):
        raise HTTPException(
            status_code=502,
            detail="Supabase administration failed: rate limited",
        )

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    monkeypatch.setattr("jawnix.api._supabase_admin", failing_admin)
    try:
        failed = TestClient(app).post(
            f"/api/admin/customers/{customer.id}/user-account-invitation",
            json={"email": "never-invited@example.com"},
        )
        assert failed.status_code == 502
        session.rollback()
        session.refresh(account)
        assert account.active is True
        assert session.scalar(
            select(func.count(UserAccountInvitation.id)).where(
                UserAccountInvitation.customer_id == customer.id
            )
        ) == 0
    finally:
        app.dependency_overrides.clear()


def test_an_identity_can_be_invited_again_after_cancellation(
    session,
    settings,
    monkeypatch,
):
    """Cancelling is not a permanent ban: the same email can be invited again."""
    incumbent_id = uuid.uuid4()
    invited_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    first = Agent(slug="reinvite-customer", name="Reinvite Customer")
    second = Agent(slug="other-customer", name="Other Customer")
    session.add_all([first, second])
    session.flush()
    session.add(
        UserAccount(
            auth_user_id=incumbent_id,
            email="incumbent@example.com",
            customer_id=first.id,
            active=True,
        )
    )
    session.add(
        UserAccount(
            auth_user_id=uuid.uuid4(),
            email="other@example.com",
            customer_id=second.id,
            active=True,
        )
    )
    session.commit()

    settings = settings.model_copy(
        update={
            "supabase_url": "https://auth.example.test",
            "supabase_service_role_key": "service-role-key",
        }
    )

    async def fake_admin(_settings, _method, _path, _payload):
        return {"id": str(invited_id)}

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    monkeypatch.setattr("jawnix.api._supabase_admin", fake_admin)
    try:
        client = TestClient(app)
        path = f"/api/admin/customers/{first.id}/user-account-invitation"
        assert client.post(path, json={"email": "invited@example.com"}).status_code == 201

        # The same identity cannot be promised to two Customers at once.
        elsewhere = client.post(
            f"/api/admin/customers/{second.id}/user-account-invitation",
            json={"email": "invited@example.com"},
        )
        assert elsewhere.status_code == 409
        assert "another" in elsewhere.json()["detail"]

        # A second invitation to the same Customer is refused, not duplicated.
        assert client.post(
            path, json={"email": "someone-else@example.com"}
        ).status_code == 409

        canceled = client.request(
            "DELETE",
            path,
            json={"reason": "Wrong address."},
        )
        assert canceled.status_code == 200
        session.refresh(session.get(UserAccount, incumbent_id))
        assert session.get(UserAccount, incumbent_id).active is True

        # Re-inviting the very same identity now succeeds.
        again = client.post(path, json={"email": "invited@example.com"})
        assert again.status_code == 201
        assert session.scalar(
            select(func.count(UserAccountInvitation.id)).where(
                UserAccountInvitation.auth_user_id == invited_id
            )
        ) == 2
        assert session.scalar(
            select(func.count(UserAccountInvitation.id)).where(
                UserAccountInvitation.auth_user_id == invited_id,
                UserAccountInvitation.status == "pending",
            )
        ) == 1
    finally:
        app.dependency_overrides.clear()


def test_directory_search_surfaces_setup_problems_and_account_standing(
    session,
):
    agency = Agency(slug="north-agency", name="North Agency")
    session.add(agency)
    session.flush()
    healthy = Agent(
        slug="healthy-customer",
        name="Healthy Customer",
        licensed_states=["TX"],
        agency_id=agency.id,
    )
    stranded = Agent(
        slug="stranded-customer",
        name="Stranded Customer",
        licensed_states=[],
    )
    session.add_all([healthy, stranded])
    session.flush()
    session.add(
        UserAccount(
            auth_user_id=uuid.uuid4(),
            email="healthy@example.com",
            customer_id=healthy.id,
            active=True,
        )
    )
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        directory = client.get("/api/admin/customers/directory").json()
        assert directory["total"] == 2
        rows = {row["slug"]: row for row in directory["customers"]}
        assert rows["healthy-customer"]["agency"] == "North Agency"
        assert rows["healthy-customer"]["account_status"]["label"] == (
            "Account active"
        )
        assert rows["healthy-customer"]["problems"] == []
        assert rows["stranded-customer"]["account_status"]["label"] == (
            "No account"
        )
        assert rows["stranded-customer"]["problems"] == [
            "No User Account has been invited",
            "No Licensed States",
        ]
        assert rows["healthy-customer"]["href"] == (
            f"/app/admin/customers/{healthy.id}"
        )

        searched = client.get(
            "/api/admin/customers/directory",
            params={"q": "healthy@example.com"},
        ).json()
        assert [row["slug"] for row in searched["customers"]] == [
            "healthy-customer"
        ]

        filtered = client.get(
            "/api/admin/customers/directory",
            params={"problems_only": True, "state": "TX"},
        ).json()
        assert filtered["customers"] == []

        by_agency = client.get(
            "/api/admin/customers/directory",
            params={"agency_id": agency.id},
        ).json()
        assert [row["slug"] for row in by_agency["customers"]] == [
            "healthy-customer"
        ]
        # No internal lifecycle vocabulary leaks into the screen contract.
        assert "pending" not in json.dumps(directory)
    finally:
        app.dependency_overrides.clear()


def test_creating_a_customer_invites_access_and_never_takes_a_password(
    session,
    settings,
    monkeypatch,
):
    admin_id = uuid.uuid4()
    invited_user_id = uuid.uuid4()
    settings = settings.model_copy(
        update={
            "public_base_url": "https://jawnix.example",
        }
    )
    provider_requests: list[tuple[str, str, dict]] = []

    async def fake_admin(_settings, method, path, payload):
        provider_requests.append((method, path, payload))
        return {"id": str(invited_user_id)}

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    monkeypatch.setattr("jawnix.api._supabase_admin", fake_admin)
    try:
        client = TestClient(app)
        created = client.post(
            "/api/admin/customers",
            json={
                "name": "Brand New Customer",
                "email": "new-user@example.com",
                "first_name": "New",
                "last_name": "User",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["slug"] == "brand-new-customer"
        assert body["userId"] == str(invited_user_id)
        # Nothing to displace, so first provisioning takes effect at once.
        assert body["mappingConfirmed"] is True

        customer = session.get(Agent, body["customerId"])
        assert customer is not None
        assert customer.licensed_states == []
        assert customer.agency_id is None
        profile = session.get(CustomerProfile, invited_user_id)
        assert profile is not None
        assert profile.licensed_states == []
        account = session.get(UserAccount, invited_user_id)
        assert account is not None
        assert account.active is True
        assert account.customer_id == customer.id

        assert provider_requests == [
            (
                "POST",
                (
                    "/auth/v1/invite?"
                    "redirect_to=https%3A%2F%2Fjawnix.example"
                    "%2Fapp%2Faccept-invitation"
                ),
                {
                    "email": "new-user@example.com",
                    "data": {"first_name": "New", "last_name": "User"},
                },
            )
        ]
        actions = {
            entry.action
            for entry in session.scalars(select(AuditEntry))
        }
        assert "customer_created" in actions
        assert "customer_user_account_provisioned" in actions
        assert {
            entry.reason
            for entry in session.scalars(select(AuditEntry))
        } == {"Created a Customer and invited its User Account"}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("extra_field", "extra_value"),
    [
        ("licensed_states", ["TX"]),
        ("reason", "Administrator selected Customer-owned data."),
    ],
)
def test_admin_cannot_assign_states_or_reason_during_customer_invitation(
    session,
    settings,
    monkeypatch,
    extra_field,
    extra_value,
):
    provider_called = False

    async def fake_admin(*_args):
        nonlocal provider_called
        provider_called = True
        return {"id": str(uuid.uuid4())}

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    monkeypatch.setattr("jawnix.api._supabase_admin", fake_admin)
    try:
        response = TestClient(app).post(
            "/api/admin/customers",
            json={
                "name": "Admin-Controlled Customer",
                "email": "new-user@example.com",
                extra_field: extra_value,
            },
        )

        assert response.status_code == 422
        assert response.json() == {
            "detail": "The Customer invitation request was invalid."
        }
        assert provider_called is False
        assert session.scalar(select(func.count(AuditEntry.id))) == 0
        assert session.scalar(select(func.count(Agent.id))) == 0
    finally:
        app.dependency_overrides.clear()


def test_admin_customer_invitation_rejects_and_redacts_known_password(
    session,
    settings,
    monkeypatch,
):
    known_password = "Known-secret-password-48"
    provider_called = False

    async def fake_admin(*_args):
        nonlocal provider_called
        provider_called = True
        return {"id": str(uuid.uuid4())}

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    monkeypatch.setattr("jawnix.api._supabase_admin", fake_admin)
    try:
        response = TestClient(app).post(
            "/api/admin/customers",
            json={
                "name": "Password Attempt Customer",
                "email": "new-user@example.com",
                "first_name": "New",
                "last_name": "User",
                "password": known_password,
            },
        )

        assert response.status_code == 422
        assert response.json() == {
            "detail": "The Customer invitation request was invalid."
        }
        assert known_password not in response.text
        assert provider_called is False
        assert session.scalar(select(func.count(AuditEntry.id))) == 0
        assert session.scalar(select(func.count(Agent.id))) == 0
    finally:
        app.dependency_overrides.clear()


def test_customer_lifecycle_blocks_hard_delete_and_erases_to_tombstone(
    session,
):
    admin_id = uuid.uuid4()
    user_id = uuid.uuid4()
    customer = Agent(slug="history-customer", name="History Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="private@example.com",
        first_name="Private",
        last_name="Person",
        phone="2145550000",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    account = UserAccount(
        auth_user_id=user_id,
        customer=customer,
        email=profile.email,
    )
    lead = Lead(phone="2145556400", title="History", state="TX")
    session.add_all([customer, profile, account, lead])
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        agent_id=customer.id,
        customer_name=customer.name,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        source="lifecycle-test",
    )
    empty = Agent(
        slug="empty-customer",
        name="Empty Customer",
        active=False,
    )
    session.add_all([event, empty])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        deactivated = client.patch(
            f"/api/admin/customers/{customer.id}",
            json={
                "name": customer.name,
                "agency_id": None,
                "active": False,
                "reason": "Customer ended service.",
            },
        )
        assert deactivated.status_code == 200
        blocked = client.request(
            "DELETE",
            f"/api/admin/customers/{customer.id}",
            json={
                "confirm_slug": customer.slug,
                "hard_delete": True,
                "reason": "Requested deletion.",
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["dependencies"]["distributions"] == 1
        refusal = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action
                == "customer_hard_delete_refused"
            )
        )
        assert refusal is not None
        assert refusal.actor_user_id == str(admin_id)
        assert refusal.reason == "Requested deletion."
        assert refusal.details["guard"] == "dependent_history"
        assert refusal.details["after"] == {"deleted": False}

        erased = client.post(
            f"/api/admin/customers/{customer.id}/erase",
            json={"reason": "Verified personal-data erasure request."},
        )
        assert erased.status_code == 200
        session.refresh(customer)
        session.refresh(profile)
        session.refresh(account)
        session.refresh(event)
        assert customer.name == "Deleted Customer"
        assert profile.first_name == profile.last_name == profile.phone == ""
        assert profile.email.endswith("@invalid.local")
        assert account.active is False
        assert event.agent_id == customer.id
        assert session.query(CustomerTombstone).filter_by(
            former_customer_id=customer.id
        ).count() == 1

        deleted = client.request(
            "DELETE",
            f"/api/admin/customers/{empty.id}",
            json={
                "confirm_slug": empty.slug,
                "hard_delete": True,
                "reason": "Unused duplicate Customer.",
            },
        )
        assert deleted.status_code == 200
        assert deleted.json()["hardDeleted"] is True
        assert session.get(Agent, empty.id) is None
    finally:
        app.dependency_overrides.clear()


def test_admin_creates_immutable_versioned_scraper_configuration(session):
    admin_id = uuid.uuid4()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        created = client.post(
            "/api/admin/scraper-configurations",
            json={
                "reason": "Add Texas roofing acquisition",
                "segments": [
                    {
                        "key": "roofing-austin-tx",
                        "niche": "Roofing",
                        "query": "roofing contractor",
                        "geography": "Austin, TX",
                        "parameters": {"pages": 4},
                    }
                ],
                "anomaly_thresholds": {
                    "down_fraction": 0.5,
                    "up_multiplier": 2.0,
                    "history_runs": 7,
                },
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["version"] == 1
        assert body["status"] == "draft"
        assert body["createdBy"] == str(admin_id)
        assert body["checksum"]
        assert body["segments"] == [
            {
                "key": "roofing-austin-tx",
                "niche": "Roofing",
                "query": "roofing contractor",
                "geography": "Austin, TX",
                "parameters": {"pages": 4},
            }
        ]

        second = client.post(
            "/api/admin/scraper-configurations",
            json={
                "reason": "Reduce acquisition pages",
                "segments": [
                    {
                        "key": "roofing-austin-tx",
                        "niche": "Roofing",
                        "query": "roofing contractor",
                        "geography": "Austin, TX",
                        "parameters": {"pages": 2},
                    }
                ],
            },
        )
        assert second.status_code == 201
        assert second.json()["version"] == 2
        assert client.patch(
            f"/api/admin/scraper-configurations/{body['id']}",
            json={"status": "active"},
        ).status_code == 405

        configurations = client.get(
            "/api/admin/scraper-configurations"
        )
        assert configurations.status_code == 200
        assert [
            item["version"] for item in configurations.json()
        ] == [2, 1]
        assert session.query(ScraperConfiguration).count() == 2
        assert session.query(SourceSegment).count() == 2
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.target_id == body["id"],
                AuditEntry.action
                == "scraper_configuration_created",
            )
        )
        assert audit is not None
        assert audit.actor_user_id == str(admin_id)
        assert audit.reason == "Add Texas roofing acquisition"
        assert audit.details["before"] is None
        assert audit.details["after"] == {
            "version": 1,
            "status": "draft",
            "segmentCount": 1,
        }
    finally:
        app.dependency_overrides.clear()


def test_admin_action_fails_visibly_when_activity_cannot_record(
    session,
    monkeypatch,
):
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )

    def fail_recording(*_args, **_kwargs):
        raise RuntimeError("activity store unavailable")

    monkeypatch.setattr("jawnix.api.record_activity", fail_recording)
    try:
        response = TestClient(
            app,
            raise_server_exceptions=False,
        ).post(
            "/api/admin/scraper-configurations",
            json={
                "reason": "This action must not pass silently.",
                "segments": [
                    {
                        "key": "visible-failure",
                        "niche": "Roofing",
                        "query": "roofing",
                        "geography": "TX",
                        "parameters": {},
                    }
                ],
            },
        )
        assert response.status_code == 500
        session.rollback()
        assert session.query(ScraperConfiguration).count() == 0
    finally:
        app.dependency_overrides.clear()


def test_configuration_schedule_manual_run_and_rollback_are_separate_audited_actions(
    session,
):
    admin_id = uuid.uuid4()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        payload = {
            "reason": "Initial configuration",
            "segments": [
                {
                    "key": "dentists-miami-fl",
                    "niche": "Dentists",
                    "query": "dentist",
                    "geography": "Miami, FL",
                    "parameters": {},
                }
            ],
        }
        first = client.post(
            "/api/admin/scraper-configurations",
            json=payload,
        ).json()
        second = client.post(
            "/api/admin/scraper-configurations",
            json={
                **payload,
                "reason": "Second configuration",
                "segments": [
                    {
                        **payload["segments"][0],
                        "parameters": {"pages": 2},
                    }
                ],
            },
        ).json()

        scheduled = client.post(
            f"/api/admin/scraper-configurations/{first['id']}/schedule",
            json={"reason": "Use on the next nightly run"},
        )
        assert scheduled.status_code == 200
        assert scheduled.json()["status"] == "scheduled"
        assert session.query(Job).filter_by(kind="run_scraper").count() == 0

        manual = client.post(
            f"/api/admin/scraper-configurations/{second['id']}/manual-run",
            json={"reason": "Validate it immediately"},
        )
        assert manual.status_code == 202
        assert manual.json()["configurationId"] == second["id"]
        run_job = session.query(Job).filter_by(kind="run_scraper").one()
        assert run_job.payload == {
            "configuration_id": second["id"],
            "reason": "Validate it immediately",
            "actor_user_id": str(admin_id),
            "manual": True,
        }
        session.refresh(session.get(ScraperConfiguration, uuid.UUID(second["id"])))
        assert session.get(
            ScraperConfiguration,
            uuid.UUID(second["id"]),
        ).status == "draft"

        rollback = client.post(
            f"/api/admin/scraper-configurations/{first['id']}/rollback",
            json={"reason": "Return to the known configuration"},
        )
        assert rollback.status_code == 201
        assert rollback.json()["version"] == 3
        assert rollback.json()["status"] == "scheduled"
        assert rollback.json()["basedOnConfigurationId"] == first["id"]
        assert session.query(AuditEntry).count() == 5
        assert [
            entry.action
            for entry in session.query(AuditEntry).order_by(AuditEntry.created_at)
        ] == [
            "scraper_configuration_created",
            "scraper_configuration_created",
            "scraper_configuration_scheduled",
            "scraper_manual_run_queued",
            "scraper_configuration_rollback_scheduled",
        ]
    finally:
        app.dependency_overrides.clear()


def test_admin_suppression_is_reversible_audited_and_controls_eligibility(
    session,
    settings,
):
    admin_id = uuid.uuid4()
    customer = Agent(slug="suppression-customer", name="Suppression Customer")
    lead = Lead(phone="2145556100", title="Suppression Lead", state="TX")
    session.add_all([customer, lead])
    session.flush()
    from conftest import make_request

    request = make_request(session, customer, 1)
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        suppressed = client.put(
            f"/api/admin/leads/{lead.id}/suppression",
            json={"reason": "Confirmed do-not-contact request"},
        )
        assert suppressed.status_code == 200
        assert suppressed.json()["suppressed"] is True
        duplicate = client.put(
            f"/api/admin/leads/{lead.id}/suppression",
            json={"reason": "Duplicate suppression request"},
        )
        assert duplicate.status_code == 200
        result = allocate_request(session, request.id, settings)
        session.commit()
        assert result.status == "waiting_inventory"
        assert result.allocated == 0

        unsuppressed = client.request(
            "DELETE",
            f"/api/admin/leads/{lead.id}/suppression",
            json={"reason": "Restriction was entered in error"},
        )
        assert unsuppressed.status_code == 200
        assert unsuppressed.json()["suppressed"] is False
        request.status = "approved"
        result = allocate_request(session, request.id, settings)
        session.commit()
        assert result.allocated == 1
        audits = session.query(AuditEntry).filter(
            AuditEntry.target_type == "lead"
        ).order_by(AuditEntry.created_at).all()
        assert [entry.action for entry in audits] == [
            "lead_suppressed",
            "lead_unsuppressed",
        ]
        assert all(entry.reason for entry in audits)
        assert all(
            entry.actor_user_id == str(admin_id)
            for entry in audits
        )
        assert audits[0].details == {
            "before": {"suppressed": False},
            "after": {"suppressed": True},
        }
        assert audits[1].details["before"]["suppressed"] is True
        assert audits[1].details["after"] == {"suppressed": False}
    finally:
        app.dependency_overrides.clear()


def test_lead_correction_overrides_delivery_until_audited_removal(
    session,
    settings,
):
    admin_id = uuid.uuid4()
    customer = Agent(slug="correction-customer", name="Correction Customer")
    lead = Lead(
        phone="2145556200",
        title="Source Title",
        state="TX",
        legacy_title="Source Title",
        legacy_state="TX",
    )
    session.add_all([customer, lead])
    session.flush()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        client = TestClient(app)
        applied = client.put(
            f"/api/admin/leads/{lead.id}/correction",
            json={
                "title": "Corrected Title",
                "state": "FL",
                "reason": "Verified with the business",
            },
        )
        assert applied.status_code == 200
        correction_id = applied.json()["correctionId"]
        session.refresh(lead)
        assert lead.title == "Corrected Title"
        assert lead.state == "FL"
        assert str(lead.active_correction_id) == correction_id

        from conftest import make_request

        request = make_request(session, customer, 1, ["FL"])
        result = allocate_request(session, request.id, settings)
        session.commit()
        assert result.allocated == 1
        event = session.scalar(
            select(DistributionEvent).where(
                DistributionEvent.request_id == request.id
            )
        )
        assert event.title == "Corrected Title"
        assert event.state == "FL"
        assert event.listing_provenance == {
            "kind": "lead_correction",
            "correctionId": correction_id,
        }

        removed = client.request(
            "DELETE",
            f"/api/admin/leads/{lead.id}/correction",
            json={"reason": "Correction no longer applies"},
        )
        assert removed.status_code == 200
        session.refresh(lead)
        assert lead.active_correction_id is None
        assert lead.title == "Source Title"
        assert lead.state == "TX"
        assert session.query(LeadCorrectionEvent).count() == 2
        assert [
            item.action
            for item in session.query(LeadCorrectionEvent).order_by(
                LeadCorrectionEvent.created_at
            )
        ] == ["applied", "removed"]
    finally:
        app.dependency_overrides.clear()


def test_customer_report_resolution_and_exact_artifact_regeneration_are_audited(
    session,
    settings,
):
    user_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    customer = Agent(slug="reported-customer", name="Reported Customer")
    profile = CustomerProfile(
        user_id=user_id,
        email="reported@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=datetime.now(timezone.utc),
    )
    lead = Lead(
        phone="2145556300",
        title="Delivered Listing",
        state="TX",
    )
    session.add_all([customer, profile, lead])
    session.flush()
    from conftest import make_request

    request = make_request(session, customer, 1, ["TX"])
    allocate_request(session, request.id, settings)
    session.commit()
    event = session.scalar(
        select(DistributionEvent).where(
            DistributionEvent.request_id == request.id
        )
    )
    artifact = session.query(BatchArtifact).filter_by(
        request_id=request.id
    ).one()
    original_bytes = __import__("pathlib").Path(artifact.path).read_bytes()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="reported@example.com",
        role="customer",
        csrf="test",
    )
    try:
        client = TestClient(app)
        submitted = client.post(
            f"/api/me/distributions/{event.id}/reports",
            json={
                "reason": "wrong_business_or_title",
                "details": "This is a different company.",
            },
        )
        assert submitted.status_code == 201
        report_id = submitted.json()["id"]
        report = session.get(LeadReport, uuid.UUID(report_id))
        assert report.details == "This is a different company."

        app.dependency_overrides[require_admin] = lambda: Principal(
            user_id=admin_id,
            email="admin@example.com",
            role="admin",
            csrf="test",
        )
        resolved = client.post(
            f"/api/admin/lead-reports/{report_id}/correct",
            json={
                "note": "Verified corrected business title.",
                "title": "Correct Business",
            },
        )
        assert resolved.status_code == 200
        session.refresh(report)
        session.refresh(lead)
        assert report.status == "corrected"
        assert report.details == "This is a different company."
        assert lead.title == "Correct Business"

        artifact.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        __import__("pathlib").Path(artifact.path).unlink()
        session.commit()
        regenerated = client.post(
            f"/api/admin/requests/{request.id}/artifact/regenerate",
            json={"reason": "Customer requested the expired batch."},
        )
        assert regenerated.status_code == 200
        assert __import__("pathlib").Path(artifact.path).read_bytes() == original_bytes
        assert regenerated.json()["sha256"] == artifact.sha256
        assert session.query(AuditEntry).filter(
            AuditEntry.action.in_(
                ["lead_report_corrected", "batch_artifact_regenerated"]
            )
        ).count() == 2
    finally:
        app.dependency_overrides.clear()


def test_same_niche_recommendation_approval_versions_configuration_without_run(
    session,
):
    admin_id = uuid.uuid4()
    customer = Agent(slug="recommendation-customer", name="Recommendation")
    configuration = ScraperConfiguration(
        version=1,
        checksum="e" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=admin_id,
        reason="Baseline",
        segments=[
            SourceSegment(
                key=key,
                niche="Roofing",
                query=key,
                geography="Texas",
                parameters={},
            )
            for key in ("roofing-a", "roofing-b")
        ],
    )
    session.add_all([customer, configuration])
    session.flush()
    for segment_index, segment in enumerate(("roofing-a", "roofing-b")):
        for index in range(100):
            lead = Lead(
                phone=f"{segment_index + 2}12{index:07d}",
                title=f"{segment} {index}",
                state="TX",
            )
            session.add(lead)
            session.flush()
            event = DistributionEvent(
                lead_id=lead.id,
                agent_id=customer.id,
                customer_name=customer.name,
                phone=lead.phone,
                title=lead.title,
                state="TX",
                source_kind="google_maps",
                source_segment_key=segment,
                source_niche="Roofing",
                distribution_period="2026-06",
                delivered_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
                source="recommendation-test",
            )
            session.add(event)
            session.flush()
            session.add(
                LeadDispositionTransition(
                    distribution_event_id=event.id,
                    customer_id=customer.id,
                    actor_user_id=admin_id,
                    disposition="no_contact",
                )
            )
            if index < 30:
                session.add(
                    LeadOutcome(
                        distribution_event_id=event.id,
                        customer_id=customer.id,
                        kind="good" if segment == "roofing-a" else "poor",
                        metric="quality",
                    )
                )
            if segment == "roofing-a" and index < 20:
                session.add(
                    LeadOutcome(
                        distribution_event_id=event.id,
                        customer_id=customer.id,
                        kind="positive_response",
                        metric="positive_response",
                    )
                )
    # Worked-lead prescriptions stay available as a decision mechanism, but
    # issue #156 deliberately leaves their automatic generation dormant.
    assert build_source_recommendations(session) == []
    expand = SourceRecommendation(
        niche="Roofing",
        segment_key="roofing-a",
        action="expand",
        evidence={
            "configurationVersion": 1,
            "prescriptiveMode": "dormant_worked_leads",
        },
        evidence_checksum="f" * 64,
        configuration_version=1,
    )
    session.add(expand)
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        response = TestClient(app).post(
            f"/api/admin/source-recommendations/{expand.id}/approve",
            json={"reason": "Expand the stronger same-niche segment."},
        )
        assert response.status_code == 200
        resulting = session.get(
            ScraperConfiguration,
            uuid.UUID(response.json()["resultingConfigurationId"]),
        )
        assert resulting.version == 2
        assert resulting.status == "scheduled"
        target = next(
            item for item in resulting.segments if item.key == "roofing-a"
        )
        assert target.parameters["recommendation_action"] == "expand"
        assert target.parameters["relative_weight"] == 1.25
        assert session.query(Job).filter_by(kind="run_scraper").count() == 0
    finally:
        app.dependency_overrides.clear()


def test_admin_session_does_not_create_customer_profile(session, settings, monkeypatch):
    admin_id = uuid.uuid4()

    async def fake_verify(_token, _settings):
        return {
            "id": str(admin_id),
            "email": "noah@jawnix.com",
            "app_metadata": {"jawnix_role": "admin"},
            "user_metadata": {"first_name": "Noah", "last_name": "Bleicher"},
        }

    def database_override():
        yield session

    class _NoFactors:
        async def list_factors(self, _user_id):
            return []

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("jawnix.api.verify_supabase_token", fake_verify)
    monkeypatch.setattr("jawnix.api.get_mfa_provider", lambda _settings: _NoFactors())
    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/session",
            json={"access_token": "test-access-token-long-enough", "requested_next": "/admin.html"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"
        # An admin with no enrolled factors is routed to MFA enrollment in the
        # shell, never to a legacy page. The profile guard still holds.
        assert response.json()["next"] == "/app/admin/mfa/enroll"
        assert session.get(CustomerProfile, admin_id) is None
    finally:
        app.dependency_overrides.clear()


def test_customer_session_cannot_target_admin_portal(session, settings, monkeypatch):
    customer_id = uuid.uuid4()

    async def fake_verify(_token, _settings):
        return {
            "id": str(customer_id),
            "email": "customer@example.com",
            "app_metadata": {"jawnix_role": "customer"},
            "user_metadata": {},
        }

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("jawnix.api.verify_supabase_token", fake_verify)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/session",
            json={"access_token": "test-access-token-long-enough", "requested_next": "/admin.html"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Sign in with noah@jawnix.com to access administration."
        assert session.get(CustomerProfile, customer_id) is None
    finally:
        app.dependency_overrides.clear()


def test_active_customer_session_uses_safe_react_destination_when_enabled(
    session,
    settings,
    monkeypatch,
):
    customer_id = uuid.uuid4()

    async def fake_verify(_token, _settings):
        return {
            "id": str(customer_id),
            "email": "customer@example.com",
            "app_metadata": {"jawnix_role": "customer"},
            "user_metadata": {},
        }

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("jawnix.api.verify_supabase_token", fake_verify)
    try:
        client = TestClient(app)
        requested = client.post(
            "/api/auth/session",
            json={
                "access_token": "test-access-token-long-enough",
                "requested_next": "/app/account",
            },
        )
        assert requested.status_code == 200
        assert requested.json()["next"] == "/app/account"

        refused_admin_path = client.post(
            "/api/auth/session",
            json={
                "access_token": "test-access-token-long-enough",
                "requested_next": "/app/admin/overview",
            },
        )
        assert refused_admin_path.status_code == 200
        assert refused_admin_path.json()["next"] == "/app/overview"
    finally:
        app.dependency_overrides.clear()


def test_inactive_user_account_cannot_establish_a_session(
    session,
    settings,
    monkeypatch,
):
    customer_id = uuid.uuid4()
    session.add(
        UserAccount(
            auth_user_id=customer_id,
            email="replaced@example.com",
            active=False,
            replaced_at=utcnow(),
        )
    )
    session.commit()

    async def fake_verify(_token, _settings):
        return {
            "id": str(customer_id),
            "email": "replaced@example.com",
            "app_metadata": {"jawnix_role": "customer"},
            "user_metadata": {},
        }

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("jawnix.api.verify_supabase_token", fake_verify)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/session",
            json={"access_token": "test-access-token-long-enough"},
        )

        assert response.status_code == 403
        assert "set-cookie" not in response.headers
        assert "jawnix_session" not in client.cookies
        assert session.get(CustomerProfile, customer_id) is None
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_authorization_replay_and_async_queue(session, settings, monkeypatch):
    user_id = uuid.uuid4()
    agent = Agent(slug="telegram-agent", name="Telegram Agent")
    profile = CustomerProfile(
        user_id=user_id,
        email="telegram@example.com",
        licensed_states=["TX"],
        agent=agent,
        mapping_confirmed_at=utcnow(),
    )
    item = LeadRequest(
        user_id=user_id,
        agent=agent,
        lead_count=10,
        state_mode="all_saved",
        states_snapshot=["TX"],
        delivery_email=profile.email,
        status="pending",
    )
    session.add_all([profile, item])
    configuration = ScraperConfiguration(
        version=999,
        checksum="f" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Telegram anomaly test",
    )
    session.add(configuration)
    session.flush()
    scrape_run = ScraperRun(
        source="google_maps",
        source_version="held",
        configuration_id=configuration.id,
        status="held_anomaly",
    )
    session.add(scrape_run)
    session.flush()
    anomaly = ScrapeAnomaly(
        scraper_run_id=scrape_run.id,
        configuration_id=configuration.id,
        dataset_checksum="e" * 64,
        status="pending",
    )
    session.add(anomaly)
    session.commit()

    configured = settings.model_copy(
        update={
            "telegram_bot_token": "test-bot-token",
            "telegram_chat_id": "6775236603",
            "telegram_approver_user_ids": "6775236603",
            "telegram_webhook_secret": "webhook-secret",
        }
    )
    monkeypatch.setattr("jawnix.telegram.TelegramClient.answer_callback", lambda *args, **kwargs: None)

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: configured
    headers = {"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"}

    def update(update_id: int, approver: str = "6775236603"):
        return {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": int(approver)},
                "message": {"chat": {"id": 6775236603}},
                "data": callback_data("approve", item.id),
            },
        }

    try:
        client = TestClient(app)
        assert client.post("/api/integrations/telegram/webhook", json=update(1)).status_code == 401
        queued = client.post("/api/integrations/telegram/webhook", headers=headers, json=update(1))
        assert queued.status_code == 200
        assert queued.json()["queued"] is True
        assert session.scalar(select(func.count(Job.id)).where(Job.kind == "telegram_action")) == 1

        duplicate = client.post("/api/integrations/telegram/webhook", headers=headers, json=update(1))
        assert duplicate.json()["duplicate"] is True
        assert session.scalar(select(func.count(Job.id)).where(Job.kind == "telegram_action")) == 1

        unauthorized = client.post(
            "/api/integrations/telegram/webhook",
            headers=headers,
            json=update(2, approver="123"),
        )
        assert unauthorized.json()["ignored"] is True
        assert session.scalar(select(func.count(Job.id)).where(Job.kind == "telegram_action")) == 1

        anomaly_update = update(3)
        anomaly_update["callback_query"]["data"] = anomaly_callback_data(
            "confirm",
            anomaly.id,
        )
        queued_anomaly = client.post(
            "/api/integrations/telegram/webhook",
            headers=headers,
            json=anomaly_update,
        )
        assert queued_anomaly.status_code == 200
        anomaly_job = session.scalar(
            select(Job).where(Job.kind == "telegram_anomaly_action")
        )
        assert anomaly_job is not None
        assert anomaly_job.request_id is None
        assert anomaly_job.payload["anomaly_id"] == str(anomaly.id)
        assert anomaly_job.payload["action"] == "confirm"
    finally:
        app.dependency_overrides.clear()
