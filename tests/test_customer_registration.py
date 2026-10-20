import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.api import app
from jawnix.auth import CSRF_COOKIE, Principal, require_admin
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import AuditEntry, Customer, CustomerHistoryIdentity, CustomerProfile, CustomerRegistration, LeadRequest, UserAccount, utcnow
from jawnix.schemas import RegistrationDecision, SignupProfile


@pytest.fixture
def signup(session, settings, monkeypatch):
    user_id = uuid.uuid4()
    identity = {"id": str(user_id), "email": "returning@example.com", "email_verified": True,
                "app_metadata": {"jawnix_role": "customer"},
                "user_metadata": {"signup_profile": {"first_name": "River", "last_name": "Morgan", "phone": "+1 (615) 555-0100", "licensed_states": ["TN", "SC"]}}}
    async def verified(*_):
        return identity
    def database():
        yield session
    monkeypatch.setattr("jawnix.api.verify_supabase_token", verified)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    try:
        yield client, identity, user_id
    finally:
        client.close()
        app.dependency_overrides.clear()


def exchange(client):
    response = client.post("/api/auth/session", json={"access_token": "verified-provider-token"})
    client.headers["X-CSRF-Token"] = client.cookies.get(CSRF_COOKIE, "")
    return response


def archive(session, name="River", last="Morgan"):
    customer = Customer(slug=f"archived-{uuid.uuid4().hex}", name=f"{name} {last}", active=False, deleted_at=utcnow(), licensed_states=["TN"])
    session.add(customer); session.flush()
    session.add(CustomerHistoryIdentity(customer_id=customer.id, first_name=name, last_name=last)); session.commit()
    return customer


def operator():
    return Principal(uuid.uuid4(), "noah@jawnix.com", "admin", "test", audience="admin")


def test_verified_signup_activates_one_customer_and_saves_details(signup, session):
    client, identity, user_id = signup
    assert {item["code"] for item in client.get("/api/auth/signup-options").json()["states"]} >= {"TN", "SC"}
    for _ in range(2):
        response = exchange(client)
        assert response.status_code == 200, response.text
        assert response.json()["next"] == "/app/overview"
    profile = session.get(CustomerProfile, user_id)
    customer = session.get(Customer, profile.agent_id)
    assert profile.phone == "6155550100"
    assert profile.first_name == "River" and profile.last_name == "Morgan"
    assert profile.licensed_states == customer.licensed_states == ["SC", "TN"]
    assert customer.active and customer.lead_database == "db1" and customer.manager_id is None and customer.agency_id is None
    assert not customer.billing_enabled
    assert session.get(UserAccount, user_id).active
    assert session.scalar(select(func.count()).select_from(Customer)) == 1
    assert session.scalar(select(func.count()).select_from(CustomerRegistration)) == 1
    assert client.get("/api/me/overview").status_code == 200
    assert client.get("/api/auth/admin-mfa").status_code == 403
    assert client.get("/api/admin/registrations").status_code == 403


def test_states_follow_verified_signup_and_resume_until_completed(signup, session):
    client, identity, user_id = signup
    identity["user_metadata"]["signup_profile"].pop("licensed_states")
    for _ in range(2):
        assert exchange(client).json()["next"] == "/app/appointed-states"
    assert client.get("/api/auth/destination").json()["next"] == "/app/appointed-states"
    assert client.get("/api/me/registration").json()["states"] == []
    assert session.get(UserAccount, user_id) is None
    assert session.scalar(select(func.count()).select_from(Customer)) == 0
    assert client.get("/api/me/overview").status_code == 409
    response = client.post("/api/me/registration/states", json={"states":["TN", "SC", "TN"]})
    assert response.status_code == 200, response.text
    assert response.json()["next"] == "/app/overview"
    assert response.json()["states"] == ["SC", "TN"]
    customer_id = session.get(UserAccount, user_id).customer_id
    assert session.get(Customer, customer_id).licensed_states == ["SC", "TN"]
    assert session.get(CustomerProfile, user_id).licensed_states == ["SC", "TN"]
    # An old provider metadata snapshot and repeated submissions cannot reset
    # state setup or bypass the later Licensed State impact review.
    assert exchange(client).json()["next"] == "/app/overview"
    assert client.post("/api/me/registration/states", json={"states":["AL"]}).json()["states"] == ["SC", "TN"]
    assert client.get("/api/me/overview").status_code == 200
    assert session.scalar(select(func.count()).select_from(Customer)) == 1
    assert session.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "customer_registration_states_selected")) == 1


def test_history_review_waits_until_states_are_selected(signup, session):
    client, identity, user_id = signup
    customer = archive(session)
    identity["user_metadata"]["signup_profile"]["licensed_states"] = []
    assert exchange(client).json()["next"] == "/app/appointed-states"
    app.dependency_overrides[require_admin] = operator
    assert client.get("/api/admin/registrations").json() == []
    assert client.post(f"/api/admin/registrations/{user_id}/decision", json={"decision":"restore", "restore_customer_id":customer.id}).status_code == 409
    assert client.post("/api/me/registration/states", json={"states":["SC"]}).json()["next"] == "/app/registration-status"
    assert not customer.active and session.get(UserAccount, user_id) is None
    assert client.get("/api/me/overview").status_code == 409
    assert len(client.get("/api/admin/registrations").json()) == 1
    assert client.post(f"/api/admin/registrations/{user_id}/decision", json={"decision":"restore", "restore_customer_id":customer.id}).status_code == 200
    assert customer.licensed_states == ["SC"]
    assert client.get("/api/me/overview").status_code == 200


@pytest.mark.parametrize("payload", [{"states":[]}, {"states":["XX"]}, {"states":[" "]}, {"states":["TN"], "auth_user_id":str(uuid.uuid4())}])
def test_state_setup_requires_valid_states_and_cannot_target_another_user(signup, session, payload):
    client, identity, user_id = signup
    identity["user_metadata"]["signup_profile"].pop("licensed_states")
    assert exchange(client).status_code == 200
    assert client.post("/api/me/registration/states", json=payload).status_code == 422
    assert session.get(CustomerRegistration, user_id).licensed_states == []
    assert session.get(UserAccount, user_id) is None


def test_state_setup_requires_session_csrf_and_open_registration(signup, session):
    client, identity, user_id = signup
    assert client.post("/api/me/registration/states", json={"states":["TN"]}).status_code == 401
    identity["user_metadata"]["signup_profile"].pop("licensed_states")
    assert exchange(client).status_code == 200
    assert client.post("/api/me/registration/states", json={"states":["TN"]}, headers={"X-CSRF-Token":"wrong"}).status_code == 403
    app.dependency_overrides[require_admin] = operator
    assert client.post(f"/api/admin/registrations/{user_id}/decision", json={"decision":"reject"}).status_code == 200
    assert client.post("/api/me/registration/states", json={"states":["TN"]}).status_code == 409
    assert client.get("/api/me/registration").json()["next"] == "/app/registration-status"
    assert session.get(UserAccount, user_id) is None


@pytest.mark.parametrize("change", [{"email_verified": False}, {"user_metadata": {"signup_profile": {"first_name": "River", "last_name": "Morgan", "phone": "bad", "licensed_states": ["XX"]}}}])
def test_unverified_or_invalid_signup_never_creates_a_customer(signup, session, change):
    client, identity, user_id = signup
    identity.update(change)
    assert exchange(client).status_code in {403, 422}
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Customer)) == 0
    assert session.get(UserAccount, user_id) is None


def test_matching_name_waits_for_operator_and_preserves_original_request_ownership(signup, session):
    client, identity, user_id = signup
    customer = archive(session)
    old_user = uuid.uuid4()
    session.add(CustomerProfile(user_id=old_user, email="old@invalid.local")); session.flush()
    request = LeadRequest(user_id=old_user, agent_id=customer.id, status="delivered", lead_count=10, states_snapshot=["TN"], delivery_email="old@example.com")
    session.add(request); session.commit()
    history_key = customer.permanent_history_key
    response = exchange(client)
    assert response.status_code == 200
    assert response.json()["next"] == "/app/registration-status"
    assert session.get(CustomerProfile, user_id).agent_id is None
    assert session.get(UserAccount, user_id) is None
    assert client.get("/api/me/registration").json()["status"] == "awaiting_review"
    assert "matches" not in client.get("/api/me/registration").json()
    for path in ["/api/me/overview", "/api/me/profile", "/api/me/batch-requests", "/api/me/billing"]:
        assert client.get(path).status_code == 409
    assert client.get(f"/api/me/batch-requests/{request.id}").status_code == 409
    app.dependency_overrides[require_admin] = operator
    pending = client.get("/api/admin/registrations").json()
    assert pending[0]["matches"][0]["id"] == customer.id
    approved = client.post(f"/api/admin/registrations/{user_id}/decision", json={"decision":"restore", "restore_customer_id":customer.id})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
    assert session.get(CustomerProfile, user_id).agent_id == customer.id
    assert customer.permanent_history_key == history_key
    assert session.get(LeadRequest, request.id).user_id == old_user
    assert client.get(f"/api/me/batch-requests/{request.id}").status_code == 200
    assert client.post(f"/api/admin/registrations/{user_id}/decision", json={"decision":"new"}).status_code == 409
    assert client.get("/api/admin/registrations").json() == []
    assert session.scalar(select(AuditEntry).where(AuditEntry.action == "customer_history_restored")).details["operatorApproved"] is True


@pytest.mark.parametrize("decision", ["new", "reject"])
def test_operator_can_keep_matching_customers_separate_or_reject(signup, session, decision):
    client, identity, user_id = signup
    customer = archive(session)
    assert exchange(client).status_code == 200
    app.dependency_overrides[require_admin] = operator
    result = client.post(f"/api/admin/registrations/{user_id}/decision", json={"decision":decision})
    assert result.status_code == 200
    assert not customer.active
    if decision == "new":
        assert session.get(CustomerProfile, user_id).agent_id != customer.id
        assert client.get("/api/me/overview").status_code == 200
    else:
        assert result.json()["status"] == "rejected"
        assert exchange(client).json()["next"] == "/app/registration-status"
        assert client.get("/api/me/overview").status_code == 409
        assert session.get(UserAccount, user_id) is None


def test_restore_rechecks_matching_customer_and_active_access(signup, session):
    client, identity, user_id = signup
    customer = archive(session)
    other = archive(session, name="Other")
    assert exchange(client).status_code == 200
    app.dependency_overrides[require_admin] = operator
    assert client.post(f"/api/admin/registrations/{user_id}/decision", json={"decision":"restore", "restore_customer_id":other.id}).status_code == 409
    customer.active = True; customer.deleted_at = None; session.commit()
    assert client.post(f"/api/admin/registrations/{user_id}/decision", json={"decision":"restore", "restore_customer_id":customer.id}).status_code == 409
    assert session.get(UserAccount, user_id) is None


@pytest.mark.parametrize("payload", [{"decision":"restore"}, {"decision":"new", "restore_customer_id":1}, {"decision":"unknown"}])
def test_registration_decision_rejects_ambiguous_payloads(payload):
    with pytest.raises(ValueError):
        RegistrationDecision.model_validate(payload)


@pytest.mark.parametrize("states_after_signup", [False, True])
def test_new_customer_receives_starting_price_once(signup, session, settings, states_after_signup):
    client, identity, user_id = signup
    settings.billing_enabled = True
    settings.db1_default_lead_rate_cents_per_thousand = 150
    if states_after_signup:
        identity["user_metadata"]["signup_profile"].pop("licensed_states")
    assert exchange(client).status_code == 200
    if states_after_signup:
        assert client.post("/api/me/registration/states", json={"states":["TN"]}).status_code == 200
    customer = session.get(Customer, session.get(UserAccount, user_id).customer_id)
    assert customer.billing_enabled and customer.lead_rate_cents_per_thousand == 150
    # Re-entering signup must preserve a later operator override.
    customer.billing_enabled = False
    customer.lead_rate_cents_per_thousand = 300
    session.commit()
    assert exchange(client).status_code == 200
    assert client.post("/api/me/registration/states", json={"states":["SC"]}).status_code == 200
    session.refresh(customer)
    assert not customer.billing_enabled and customer.lead_rate_cents_per_thousand == 300


def test_starting_rate_does_not_enable_billing_when_platform_disabled(signup, session, settings):
    client, identity, user_id = signup
    settings.billing_enabled = False
    settings.db1_default_lead_rate_cents_per_thousand = 150
    assert exchange(client).status_code == 200
    customer = session.get(Customer, session.get(UserAccount, user_id).customer_id)
    assert not customer.billing_enabled and customer.lead_rate_cents_per_thousand is None
