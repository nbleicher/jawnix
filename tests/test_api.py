from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import Agent, CustomerProfile, Job, LeadRequest, utcnow
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
