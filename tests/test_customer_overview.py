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
    BatchArtifact,
    CustomerProfile,
    DistributionEvent,
    Lead,
    LeadDispositionTransition,
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


def _artifact(session, tmp_path, request, *, expires_at: datetime):
    path = tmp_path / f"{request.id}.csv"
    path.write_text("phone,title\n2125550100,Example\n")
    artifact = BatchArtifact(
        request=request,
        path=str(path),
        filename="customer_batch.csv",
        row_count=request.lead_count,
        byte_count=path.stat().st_size,
        sha256="a" * 64,
        expires_at=expires_at,
    )
    session.add(artifact)
    return artifact


def test_overview_queues_an_undownloaded_live_batch_artifact(session, tmp_path):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"])
    now = datetime.now(timezone.utc)
    item = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=750,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email="customer@example.com",
        status=RequestStatus.delivered.value,
        delivered_at=now - timedelta(days=1),
    )
    session.add(item)
    session.flush()
    _artifact(session, tmp_path, item, expires_at=now + timedelta(days=20))
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": f"batch-ready:{item.id}",
                "kind": "batch_ready",
                "title": "Your Batch is ready",
                "description": "Download the 750-lead Batch Artifact.",
                "tone": "info",
                "action": {
                    "kind": "download_artifact",
                    "label": "Download CSV",
                    "description": "Download this Batch Artifact while it is live.",
                    "href": f"/api/me/batch-requests/{item.id}/artifact",
                },
            }
        ]
    }


def test_overview_escalates_an_undownloaded_artifact_expiring_soon(
    session,
    tmp_path,
):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"])
    now = datetime.now(timezone.utc)
    item = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=500,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email="customer@example.com",
        status=RequestStatus.delivered.value,
        delivered_at=now - timedelta(days=27),
    )
    session.add(item)
    session.flush()
    _artifact(session, tmp_path, item, expires_at=now + timedelta(days=3))
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": f"artifact-expiring:{item.id}",
            "kind": "artifact_expiring",
            "title": "Batch Artifact expires soon",
            "description": "Download the 500-lead Batch Artifact within 3 days.",
            "tone": "warning",
            "action": {
                "kind": "download_artifact",
                "label": "Download CSV",
                "description": "Download this Batch Artifact before it expires.",
                "href": f"/api/me/batch-requests/{item.id}/artifact",
            },
        }
    ]


def test_overview_queues_a_request_waiting_for_inventory(session):
    user_id, customer, _, _ = _customer(
        session,
        licensed_states=["FL", "TX"],
    )
    item = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=250,
        states_snapshot=["TX", "FL"],
        state_mode="all_saved",
        delivery_email="customer@example.com",
        status=RequestStatus.waiting_inventory.value,
    )
    session.add(item)
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": f"waiting-inventory:{item.id}",
            "kind": "waiting_inventory",
            "title": "Batch Request is waiting for inventory",
            "description": "Review or cancel your 250-lead request for FL, TX.",
            "tone": "warning",
            "action": {
                "kind": "review_request",
                "label": "Review request",
                "description": "Open this Batch Request's detail page.",
                "href": f"/app/requests?request={item.id}",
            },
        }
    ]


def test_overview_nudges_for_feedback_after_a_batch_is_downloaded(
    session,
    tmp_path,
):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"])
    now = datetime.now(timezone.utc)
    item = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=1,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email="customer@example.com",
        status=RequestStatus.delivered.value,
        delivered_at=now - timedelta(days=2),
    )
    lead = Lead(phone="2145550100", title="Example Roofing", state="TX")
    session.add_all([item, lead])
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        customer_id=customer.id,
        customer_name=customer.name,
        request_id=item.id,
        phone=lead.phone,
        title=lead.title,
        state=lead.state,
        delivered_at=item.delivered_at,
        source="batch",
    )
    session.add(event)
    _artifact(session, tmp_path, item, expires_at=now + timedelta(days=28))
    session.commit()

    client = _authenticate(session, user_id)
    try:
        download = client.get(
            f"/api/me/batch-requests/{item.id}/artifact"
        )
        response = client.get("/api/me/overview")
        session.add(
            LeadDispositionTransition(
                distribution_event_id=event.id,
                customer_id=customer.id,
                actor_user_id=user_id,
                disposition="positive_response",
            )
        )
        session.commit()
        settled = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert download.status_code == 200
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": f"feedback-nudge:{item.id}",
            "kind": "feedback_nudge",
            "title": "How did this Batch perform?",
            "description": "Share one lead outcome from this 1-lead Batch.",
            "tone": "info",
            "action": {
                "kind": "submit_feedback",
                "label": "Give feedback",
                "description": "Record one Lead Disposition or Quality Rating.",
                "href": f"/app/feedback?request={item.id}",
            },
        }
    ]
    assert settled.json() == {"items": []}


def test_overview_queues_a_setup_problem_that_the_customer_can_fix(session):
    user_id, _, _, _ = _customer(session, licensed_states=[])
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": "setup-problem:no-licensed-states",
            "kind": "setup_problem",
            "title": "Add Licensed States",
            "description": (
                "Add at least one Licensed State before requesting a Batch."
            ),
            "tone": "warning",
            "action": {
                "kind": "add_licensed_states",
                "label": "Open Account",
                "description": "Add the states where you are licensed.",
                "href": "/app/account",
            },
        }
    ]


@pytest.mark.parametrize(
    "backend_status",
    [
        RequestStatus.pending.value,
        RequestStatus.approved.value,
        RequestStatus.processing.value,
        RequestStatus.generated.value,
        RequestStatus.delivered.value,
        RequestStatus.rejected.value,
        RequestStatus.canceled.value,
        RequestStatus.failed.value,
        "future_internal_state",
    ],
)
def test_overview_never_leaks_non_actionable_request_statuses_into_the_queue(
    session,
    backend_status,
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
    assert response.json() == {"items": []}
    assert f"internal-known-state-material-{backend_status}" not in response.text


def test_overview_is_calm_and_empty_when_nothing_needs_the_customer(session):
    user_id, _, _, _ = _customer(session, licensed_states=["TX"])
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"items": []}


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
