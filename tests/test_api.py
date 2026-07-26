from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    Agent,
    CustomerProfile,
    DistributionEvent,
    Job,
    Lead,
    LeadOutcome,
    LeadRequest,
    utcnow,
)
from jawnix.telegram import callback_data


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

        agent.active = False
        session.commit()
        assert client.post(
            "/api/me/requests",
            json={"lead_count": 10, "state_mode": "selected", "states": ["TX"]},
        ).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_customer_state_removal_narrows_unallocated_requests_without_expanding_them(session):
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
        assert response.status_code == 200

        session.refresh(narrowed)
        session.refresh(canceled)
        session.refresh(committed)
        assert narrowed.states_snapshot == ["FL"]
        assert narrowed.status == "approved"
        assert narrowed.approved_at is not None
        assert "TX" in narrowed.status_message
        assert canceled.states_snapshot == []
        assert canceled.status == "canceled"
        assert committed.states_snapshot == ["TX", "FL"]

        updates = list(
            session.scalars(
                select(Job)
                .where(Job.kind == "licensed_states_changed")
                .order_by(Job.id)
            )
        )
        assert len(updates) == 2
        assert updates[0].request_id == narrowed.id
        assert updates[0].payload == {
            "added": ["CA"],
            "removed": ["TX"],
            "requestAction": "narrowed",
            "states": ["FL"],
        }
        assert updates[1].request_id == canceled.id
        assert updates[1].payload["requestAction"] == "canceled"
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
        assert performance.json() == {
            "segments": [
                {
                    "segment": "roofing|TX|maps",
                    "niche": "roofing",
                    "delivered": 1,
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
            ],
            "legacy": {
                "delivered": 1,
                "excludedFromRecommendations": True,
            },
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
        assert client.post(f"/api/admin/recipients/{uuid.uuid4()}/send-password-reset").status_code == 404
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

        agent_update = client.patch(
            f"/api/admin/agents/{agent.id}",
            json={"name": "Renamed Agent", "agency_id": second_agency.id, "active": True},
        )
        assert agent_update.status_code == 200
        session.refresh(agent)
        assert agent.name == "Renamed Agent"
        assert agent.agency_id == second_agency.id
        assert agent.active is True

        assert client.patch(
            f"/api/admin/agents/{agent.id}",
            json={"name": "Agent", "agency_id": 999_999, "active": True},
        ).status_code == 404
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
        session.add(active_request)
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
            "deletedAgents": 1,
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

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("jawnix.api.verify_supabase_token", fake_verify)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/session",
            json={"access_token": "test-access-token-long-enough", "requested_next": "/admin.html"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"
        assert response.json()["next"] == "/admin.html"
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
    finally:
        app.dependency_overrides.clear()
