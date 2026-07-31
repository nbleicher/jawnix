from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    AuditEntry,
    CustomerProfile,
    Job,
    LeadRequest,
    RequestStatus,
    utcnow,
)


def _customer_with_requests(session):
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"licensed-{user_id}",
        name="Licensed State Customer",
        licensed_states=["FL", "TX"],
    )
    profile = CustomerProfile(
        user_id=user_id,
        email=f"{user_id}@example.com",
        first_name="Licensed",
        last_name="Customer",
        licensed_states=["FL", "TX"],
        agent=customer,
        mapping_confirmed_at=utcnow(),
    )
    narrowed = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=100,
        state_mode="all_saved",
        states_snapshot=["FL", "TX"],
        delivery_email=profile.email,
        status=RequestStatus.approved.value,
        approved_at=utcnow(),
    )
    canceled = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=50,
        state_mode="selected",
        states_snapshot=["TX"],
        delivery_email=profile.email,
        status=RequestStatus.waiting_inventory.value,
        approved_at=utcnow(),
    )
    committed = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=25,
        state_mode="all_saved",
        states_snapshot=["FL", "TX"],
        delivery_email=profile.email,
        status=RequestStatus.generated.value,
        approved_at=utcnow(),
        processed_at=utcnow(),
    )
    session.add_all([customer, profile, narrowed, canceled, committed])
    session.commit()
    return user_id, customer, profile, narrowed, canceled, committed


def _client(session, settings, *, user_id: uuid.UUID, email: str) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email=email,
        role="customer",
        csrf="test",
    )
    return TestClient(app)


def _preview(client: TestClient, *, states: list[str]) -> dict:
    account = client.get("/api/me/licensed-states")
    assert account.status_code == 200
    response = client.post(
        "/api/me/licensed-states/preview",
        json={
            "states": states,
            "expected_version": account.json()["version"],
        },
    )
    assert response.status_code == 200
    return response.json()


def test_account_workspace_is_loader_ready_and_searchable_by_name(session, settings):
    user_id, _, profile, *_ = _customer_with_requests(session)
    client = _client(session, settings, user_id=user_id, email=profile.email)
    try:
        response = client.get("/api/me/licensed-states")
        assert response.status_code == 200
        body = response.json()
        assert body["states"] == ["FL", "TX"]
        assert body["version"] == profile.updated_at.isoformat()
        assert {"code": "DC", "name": "District of Columbia"} in body["options"]
        assert {"code": "TX", "name": "Texas"} in body["options"]
        assert len(body["options"]) == 51
    finally:
        app.dependency_overrides.clear()


def test_preview_names_every_affected_request_without_mutating(session, settings):
    user_id, customer, profile, narrowed, canceled, committed = (
        _customer_with_requests(session)
    )
    client = _client(session, settings, user_id=user_id, email=profile.email)
    try:
        review = _preview(client, states=["CA", "FL"])
        assert review["current_states"] == ["FL", "TX"]
        assert review["proposed_states"] == ["CA", "FL"]
        assert review["added_states"] == ["CA"]
        assert review["removed_states"] == ["TX"]
        assert review["additions_apply_to_future_requests_only"] is True
        assert review["impacts"] == [
            {
                "request_id": str(narrowed.id),
                "lead_count": 100,
                "status": "Under Review",
                "current_states": ["FL", "TX"],
                "resulting_states": ["FL"],
                "action": "narrowed",
            },
            {
                "request_id": str(canceled.id),
                "lead_count": 50,
                "status": "Preparing Batch",
                "current_states": ["TX"],
                "resulting_states": [],
                "action": "canceled",
            },
        ]
        assert review["review_token"]

        session.refresh(profile)
        session.refresh(customer)
        session.refresh(narrowed)
        session.refresh(canceled)
        session.refresh(committed)
        assert profile.licensed_states == ["FL", "TX"]
        assert customer.licensed_states == ["FL", "TX"]
        assert narrowed.states_snapshot == ["FL", "TX"]
        assert canceled.states_snapshot == ["TX"]
        assert committed.states_snapshot == ["FL", "TX"]
    finally:
        app.dependency_overrides.clear()


def test_confirmed_review_applies_and_returns_all_customer_aggregates_atomically(
    session,
    settings,
):
    user_id, customer, profile, narrowed, canceled, committed = (
        _customer_with_requests(session)
    )
    client = _client(session, settings, user_id=user_id, email=profile.email)
    try:
        review = _preview(client, states=["CA", "FL"])
        response = client.post(
            "/api/me/licensed-states/apply",
            json={"review_token": review["review_token"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["account"]["states"] == ["CA", "FL"]
        assert body["overview"]["items"] == []
        by_id = {
            item["id"]: item
            for item in body["requests"]["requests"]
        }
        assert by_id[str(narrowed.id)]["states"] == ["FL"]
        assert by_id[str(canceled.id)]["states"] == []
        assert by_id[str(canceled.id)]["status"]["label"] == "Canceled"
        assert by_id[str(committed.id)]["states"] == ["FL", "TX"]

        session.refresh(profile)
        session.refresh(customer)
        session.refresh(narrowed)
        session.refresh(canceled)
        session.refresh(committed)
        assert profile.licensed_states == ["CA", "FL"]
        assert customer.licensed_states == ["CA", "FL"]
        assert narrowed.states_snapshot == ["FL"]
        assert narrowed.status == RequestStatus.approved.value
        assert canceled.states_snapshot == []
        assert canceled.status == RequestStatus.canceled.value
        assert canceled.closed_at is not None
        assert committed.states_snapshot == ["FL", "TX"]

        jobs = list(
            session.scalars(
                select(Job)
                .where(Job.kind == "licensed_states_changed")
                .order_by(Job.id)
            )
        )
        assert [job.request_id for job in jobs] == [narrowed.id, canceled.id]
        assert [job.payload["requestAction"] for job in jobs] == [
            "narrowed",
            "canceled",
        ]
        activity = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "licensed_states_update"
            )
        )
        assert activity is not None
        assert activity.actor_user_id == str(user_id)
        assert activity.details == {
            "before": ["FL", "TX"],
            "after": ["CA", "FL"],
            "added": ["CA"],
            "removed": ["TX"],
            "requestUpdates": [
                {
                    "requestId": str(narrowed.id),
                    "action": "narrowed",
                    "before": ["FL", "TX"],
                    "after": ["FL"],
                },
                {
                    "requestId": str(canceled.id),
                    "action": "canceled",
                    "before": ["TX"],
                    "after": [],
                },
            ],
        }
    finally:
        app.dependency_overrides.clear()


def test_additions_apply_only_to_future_requests(session, settings):
    user_id, _, profile, narrowed, canceled, committed = _customer_with_requests(
        session
    )
    client = _client(session, settings, user_id=user_id, email=profile.email)
    try:
        review = _preview(client, states=["CA", "FL", "TX"])
        assert review["impacts"] == []
        response = client.post(
            "/api/me/licensed-states/apply",
            json={"review_token": review["review_token"]},
        )
        assert response.status_code == 200
        for item in (narrowed, canceled, committed):
            session.refresh(item)
        assert narrowed.states_snapshot == ["FL", "TX"]
        assert canceled.states_snapshot == ["TX"]
        assert committed.states_snapshot == ["FL", "TX"]
    finally:
        app.dependency_overrides.clear()


def test_concurrent_profile_update_refuses_the_whole_apply(session, settings):
    user_id, customer, profile, narrowed, canceled, _ = _customer_with_requests(
        session
    )
    client = _client(session, settings, user_id=user_id, email=profile.email)
    try:
        review = _preview(client, states=["FL"])
        profile.licensed_states = ["CA", "FL", "TX"]
        customer.licensed_states = ["CA", "FL", "TX"]
        profile.updated_at = utcnow()
        session.commit()

        response = client.post(
            "/api/me/licensed-states/apply",
            json={"review_token": review["review_token"]},
        )
        assert response.status_code == 409
        assert "another session" in response.json()["detail"]

        session.refresh(profile)
        session.refresh(narrowed)
        session.refresh(canceled)
        assert profile.licensed_states == ["CA", "FL", "TX"]
        assert narrowed.states_snapshot == ["FL", "TX"]
        assert canceled.states_snapshot == ["TX"]
        assert session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "licensed_states_update"
            )
        ) is None
    finally:
        app.dependency_overrides.clear()


def test_concurrent_request_update_invalidates_the_exact_impact(session, settings):
    user_id, _, profile, narrowed, canceled, _ = _customer_with_requests(session)
    client = _client(session, settings, user_id=user_id, email=profile.email)
    try:
        review = _preview(client, states=["FL"])
        narrowed.status = RequestStatus.generated.value
        session.commit()

        response = client.post(
            "/api/me/licensed-states/apply",
            json={"review_token": review["review_token"]},
        )
        assert response.status_code == 409
        assert "Batch Request changed" in response.json()["detail"]

        session.refresh(profile)
        session.refresh(narrowed)
        session.refresh(canceled)
        assert profile.licensed_states == ["FL", "TX"]
        assert narrowed.states_snapshot == ["FL", "TX"]
        assert canceled.states_snapshot == ["TX"]
    finally:
        app.dependency_overrides.clear()


def test_apply_requires_a_valid_preview_artifact(session, settings):
    user_id, _, profile, *_ = _customer_with_requests(session)
    client = _client(session, settings, user_id=user_id, email=profile.email)
    try:
        response = client.post(
            "/api/me/licensed-states/apply",
            json={"review_token": "not-a-signed-review"},
        )
        assert response.status_code == 422
        assert response.json() == {
            "detail": "This impact review is invalid. Review the changes again."
        }
        session.refresh(profile)
        assert profile.licensed_states == ["FL", "TX"]
    finally:
        app.dependency_overrides.clear()
