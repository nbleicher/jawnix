from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    CustomerProfile,
    LeadRequest,
    RequestStatus,
    UserAccount,
)


def _authenticate(session, user_id: uuid.UUID) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="customer@example.com",
        role="customer",
        csrf="test",
    )
    return TestClient(app)


def _customer(session, *, licensed_states: list[str] | None = None):
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"overview-{user_id}",
        name="Overview Customer",
        licensed_states=licensed_states or [],
    )
    profile = CustomerProfile(
        user_id=user_id,
        email="customer@example.com",
        first_name="  Casey  ",
        licensed_states=licensed_states or [],
        agent=customer,
        mapping_confirmed_at=datetime.now(timezone.utc),
    )
    account = UserAccount(
        auth_user_id=user_id,
        email=profile.email,
        customer=customer,
        active=True,
    )
    session.add_all([customer, profile, account])
    session.flush()
    return user_id, customer, profile, account


@pytest.mark.parametrize(
    ("backend_status", "label", "tone"),
    [
        (RequestStatus.pending.value, "Submitted", "info"),
        (RequestStatus.approved.value, "Under Review", "info"),
        (RequestStatus.processing.value, "Preparing Batch", "warning"),
        (RequestStatus.waiting_inventory.value, "Preparing Batch", "warning"),
        (RequestStatus.generated.value, "Preparing Batch", "warning"),
        (RequestStatus.delivered.value, "Delivered", "success"),
        (RequestStatus.rejected.value, "Not Approved", "danger"),
        (RequestStatus.canceled.value, "Canceled", "neutral"),
        (RequestStatus.failed.value, "Needs Attention", "danger"),
        ("future_internal_state", "Under Review", "info"),
    ],
)
def test_overview_translates_every_request_status_at_the_contract(
    session,
    backend_status,
    label,
    tone,
):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"])
    request = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=250,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email="customer@example.com",
        status=backend_status,
        status_message=f"internal-known-state-material-{backend_status}",
    )
    session.add(request)
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    current = response.json()["current_request"]
    assert current["status"]["label"] == label
    assert current["status"]["tone"] == tone
    assert set(current["status"]) == {"label", "description", "tone"}
    assert "status_message" not in current
    assert f"internal-known-state-material-{backend_status}" not in response.text
    if backend_status == RequestStatus.waiting_inventory.value:
        assert "waiting_inventory" not in response.text
        assert "nothing you need to do" in current["status"]["description"]
    if backend_status == "future_internal_state":
        assert backend_status not in response.text


def test_overview_returns_current_work_and_recent_deliveries_once(session):
    user_id, customer, _, _ = _customer(
        session,
        licensed_states=["ny", "TX", "NY"],
    )
    start = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    delivered_ids = []
    for index in range(4):
        item = LeadRequest(
            user_id=user_id,
            agent=customer,
            lead_count=(index + 1) * 100,
            states_snapshot=["TX"],
            state_mode="all_saved",
            delivery_email="customer@example.com",
            status=RequestStatus.delivered.value,
            created_at=start + timedelta(days=index),
            delivered_at=start + timedelta(days=index, hours=1),
        )
        session.add(item)
        session.flush()
        delivered_ids.append(item.id)
    current = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=750,
        states_snapshot=["NY", "TX"],
        state_mode="selected",
        delivery_email="customer@example.com",
        status=RequestStatus.waiting_inventory.value,
        status_message="only 17 matching internal rows",
        created_at=start + timedelta(days=5),
    )
    session.add(current)
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Casey"
    assert body["licensed_states"] == ["NY", "TX"]
    assert body["current_request"] == {
        "id": str(current.id),
        "lead_count": 750,
        "states": ["NY", "TX"],
        "submitted_at": current.created_at.isoformat().replace("+00:00", "Z"),
        "delivered_at": None,
        "status": {
            "label": "Preparing Batch",
            "description": (
                "We are waiting for enough matching leads. "
                "There is nothing you need to do."
            ),
            "tone": "warning",
        },
    }
    assert [
        item["request_id"] for item in body["recent_deliveries"]
    ] == [str(item) for item in reversed(delivered_ids[1:])]
    assert body["next_action"]["kind"] == "review_request"
    assert [action["kind"] for action in body["primary_actions"]] == [
        "request_batch",
        "submit_feedback",
    ]
    assert "only 17 matching internal rows" not in response.text


def test_overview_treats_empty_customer_data_as_a_valid_result(session):
    user_id, _, profile, _ = _customer(session)
    profile.mapping_confirmed_at = None
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["licensed_states"] == []
    assert body["current_request"] is None
    assert body["recent_deliveries"] == []
    assert body["next_action"]["kind"] == "review_account"


@pytest.mark.parametrize(
    ("request_status", "licensed_states", "confirmed", "expected_action"),
    [
        (None, ["TX"], True, "request_batch"),
        (None, [], True, "add_licensed_states"),
        (None, ["TX"], False, "review_account"),
        (RequestStatus.pending.value, ["TX"], True, "review_request"),
        (RequestStatus.processing.value, ["TX"], True, "review_request"),
        (RequestStatus.delivered.value, ["TX"], True, "submit_feedback"),
        (RequestStatus.rejected.value, ["TX"], True, "request_batch"),
        (RequestStatus.canceled.value, ["TX"], True, "request_batch"),
        (RequestStatus.failed.value, ["TX"], True, "review_request"),
    ],
)
def test_overview_identifies_the_next_available_action(
    session,
    request_status,
    licensed_states,
    confirmed,
    expected_action,
):
    user_id, customer, profile, _ = _customer(
        session,
        licensed_states=licensed_states,
    )
    if not confirmed:
        profile.mapping_confirmed_at = None
    if request_status is not None:
        session.add(
            LeadRequest(
                user_id=user_id,
                agent=customer,
                lead_count=250,
                states_snapshot=["TX"],
                state_mode="all_saved",
                delivery_email="customer@example.com",
                status=request_status,
            )
        )
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["next_action"]["kind"] == expected_action


def test_overview_refuses_a_replaced_or_deactivated_user_account(session):
    user_id, _, _, account = _customer(session, licensed_states=["TX"])
    account.active = False
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
