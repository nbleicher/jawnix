import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agent, AuditEntry, CustomerHistoryIdentity, CustomerProfile, CustomerTombstone,
    DistributionEvent, Lead, LeadRequest, UserAccount, utcnow,
)
from jawnix.allocation import eligible_query
from jawnix.customer_history import archived_customer_matches


@pytest.fixture
def restoration(session, settings, monkeypatch):
    old_id, new_id = uuid.uuid4(), uuid.uuid4()
    customer = Agent(slug="returning-customer", name="River Morgan", active=False,
                     deleted_at=utcnow(), licensed_states=["TN"],
                     billing_enabled=True, lead_rate_cents_per_thousand=500)
    session.add(customer)
    session.flush()
    old_profile = CustomerProfile(user_id=old_id, email=f"archived-{old_id}@invalid.local",
                                  first_name="River", last_name="Morgan", agent_id=None,
                                  licensed_states=["TN"])
    account = UserAccount(auth_user_id=old_id, email="old@example.com", customer_id=customer.id, active=False)
    identity = CustomerHistoryIdentity(customer_id=customer.id, first_name="River", last_name="Morgan")
    lead = Lead(phone="6155550101", title="Delivered listing", state="TN")
    session.add_all([old_profile, account, identity, lead])
    session.flush()
    request = LeadRequest(user_id=old_id, agent_id=customer.id, lead_count=1, rows_per_file=1,
                          states_snapshot=["TN"], delivery_email="old@example.com", status="delivered")
    session.add(request)
    session.flush()
    event = DistributionEvent(lead_id=lead.id, agent_id=customer.id, request_id=request.id,
                              customer_name=customer.name, phone=lead.phone, title=lead.title,
                              state="TN", source="legacy", delivered_at=utcnow() - timedelta(days=60))
    session.add(event)
    session.commit()
    calls = []

    async def dispatch(_settings, payload):
        calls.append(str(payload.email))
        return new_id

    def database():
        yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(uuid.uuid4(), "noah@jawnix.com", "admin", "test")
    monkeypatch.setattr("jawnix.api._dispatch_invitation", dispatch)
    payload = {"name":"River Morgan", "first_name":"River", "last_name":"Morgan", "email":"old@example.com"}
    try:
        yield TestClient(app), customer, old_id, new_id, request, event, calls, payload
    finally:
        app.dependency_overrides.clear()


def test_exact_name_requires_operator_choice_before_inviting_or_restoring(restoration, session):
    client, customer, old_id, new_id, request, event, calls, payload = restoration
    response = client.post("/api/admin/customers", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["archivedCustomers"][0]["id"] == customer.id
    assert calls == []
    assert not customer.active and customer.deleted_at is not None
    assert session.scalar(select(func.count()).select_from(AuditEntry)) == 0
    assert session.get(UserAccount, new_id) is None


def test_approved_restoration_reuses_history_and_refuses_the_retired_login(restoration, session, settings):
    client, customer, old_id, new_id, request, event, calls, payload = restoration
    key = customer.permanent_history_key
    result = client.post("/api/admin/customers", json={**payload, "restore_customer_id":customer.id})
    assert result.status_code == 201
    assert result.json()["historyRestored"] is True
    assert result.json()["customerId"] == customer.id
    assert calls == ["old@example.com"]
    assert customer.active and customer.deleted_at is None
    assert customer.permanent_history_key == key
    assert customer.billing_enabled and customer.lead_rate_cents_per_thousand == 500
    assert session.get(CustomerProfile,new_id).licensed_states == ["TN"]
    assert session.get(LeadRequest,request.id).user_id == old_id
    assert session.get(DistributionEvent,event.id).agent_id == customer.id
    assert not session.get(UserAccount,old_id).active
    audit = session.scalar(select(AuditEntry).where(AuditEntry.action == "customer_history_restored"))
    assert audit.details["operatorApproved"] is True

    app.dependency_overrides[require_principal] = lambda: Principal(new_id,"old@example.com","customer","test")
    workspace = client.get("/api/me/batch-requests")
    assert workspace.status_code == 200
    assert [row["id"] for row in workspace.json()["requests"]] == [str(request.id)]
    assert client.get(f"/api/me/batch-requests/{request.id}").status_code == 200
    assert len(client.get("/api/me/requests").json()) == 1
    # The immutable original event still makes this phone permanently
    # ineligible for the restored party, regardless of the new login UUID.
    assert session.scalar(eligible_query(request,settings).where(Lead.id == event.lead_id)) is None

    other_id = uuid.uuid4()
    session.add(CustomerProfile(user_id=other_id,email="unmapped@example.com"))
    session.commit()
    app.dependency_overrides[require_principal] = lambda: Principal(other_id,"unmapped@example.com","customer","test")
    assert client.get(f"/api/me/batch-requests/{request.id}").status_code == 404
    assert client.get("/api/me/batch-requests").json()["requests"] == []

    app.dependency_overrides[require_principal] = lambda: Principal(old_id,"old@example.com","customer","test")
    for path in ["/api/me/profile", "/api/me/requests", "/api/me/batch-requests", "/api/me/billing"]:
        assert client.get(path).status_code == 403
    assert client.post("/api/me/feedback/lookup",json={"phone":"6155550101"}).status_code == 403


def test_duplicate_names_require_selection_and_can_create_a_separate_party(restoration, session):
    client, customer, old_id, new_id, request, event, calls, payload = restoration
    other = Agent(slug="different-river",name="River Morgan",active=False,deleted_at=utcnow())
    session.add(other)
    session.flush()
    session.add(CustomerHistoryIdentity(customer_id=other.id,first_name="River",last_name="Morgan"))
    session.commit()
    matches = client.post("/api/admin/customers",json=payload).json()["detail"]["archivedCustomers"]
    assert {row["id"] for row in matches} == {customer.id, other.id}
    response = client.post("/api/admin/customers",json={**payload,"create_new_identity":True})
    assert response.status_code == 201
    assert response.json()["customerId"] not in {customer.id,other.id}
    assert response.json()["historyRestored"] is False
    assert not customer.active and not other.active


def test_erased_and_nonmatching_histories_cannot_be_claimed(restoration, session):
    client, customer, old_id, new_id, request, event, calls, payload = restoration
    for changed in [{"first_name":"river"}, {"last_name":"Different"}, {"first_name":""}]:
        response = client.post("/api/admin/customers",json={**payload,**changed,"restore_customer_id":customer.id})
        assert response.status_code == 409
    session.add(CustomerTombstone(former_customer_id=customer.id,opaque_key="erased-history",actor_id="operator",reason="Erased"))
    session.commit()
    assert archived_customer_matches(session,"River","Morgan") == []
    assert client.post("/api/admin/customers",json={**payload,"restore_customer_id":customer.id}).status_code == 409
    assert calls == []


def test_failed_invitation_leaves_customer_archived(restoration, session, monkeypatch):
    from fastapi import HTTPException
    client, customer, old_id, new_id, request, event, calls, payload = restoration

    async def refused(*args):
        raise HTTPException(status_code=502,detail="Invitation unavailable")

    monkeypatch.setattr("jawnix.api._dispatch_invitation",refused)
    response = client.post("/api/admin/customers",json={**payload,"restore_customer_id":customer.id})
    assert response.status_code == 502
    assert not customer.active and customer.deleted_at is not None
    assert session.get(UserAccount,new_id) is None
    assert session.scalar(select(func.count()).select_from(AuditEntry)) == 0
