from __future__ import annotations

import csv
import hashlib
import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from jawnix.api import app
from jawnix.allocation import allocate_request
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import get_settings
from jawnix.customer_requests import build_milestones
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    Agent,
    AuditEntry,
    BatchArtifact,
    CustomerProfile,
    Job,
    Lead,
    LeadRequest,
    RequestStatus,
    UserAccount,
)


SUBMITTED_AT = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


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


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _customer(
    session,
    *,
    licensed_states: list[str] | None = None,
    confirmed: bool = True,
):
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"requests-{user_id}",
        name="Requests Customer",
        licensed_states=licensed_states or [],
    )
    profile = CustomerProfile(
        user_id=user_id,
        email=f"{user_id}@example.com",
        first_name="Casey",
        licensed_states=licensed_states or [],
        agent=customer,
        mapping_confirmed_at=(
            datetime.now(timezone.utc) if confirmed else None
        ),
    )
    account = UserAccount(
        auth_user_id=user_id,
        email=profile.email,
        customer=customer,
        active=True,
    )
    session.add_all([customer, profile, account])
    session.flush()
    return user_id, customer, profile


def _request(
    session,
    user_id: uuid.UUID,
    customer: Agent,
    *,
    status: str,
    states: list[str] | None = None,
    lead_count: int = 500,
    rows_per_file: int | None = None,
    approved_at: datetime | None = None,
    processed_at: datetime | None = None,
    delivered_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> LeadRequest:
    item = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=lead_count,
        rows_per_file=rows_per_file or lead_count,
        state_mode="all_saved",
        states_snapshot=states or ["TX"],
        delivery_email="customer@example.com",
        status=status,
        created_at=SUBMITTED_AT,
        approved_at=approved_at,
        processed_at=processed_at,
        delivered_at=delivered_at,
        closed_at=closed_at,
    )
    session.add(item)
    session.flush()
    return item


def _artifact(
    session,
    tmp_path,
    request: LeadRequest,
    *,
    expires_at: datetime,
) -> tuple[BatchArtifact, bytes]:
    csv_contents = b"phone,title\n2145550100,Portal Lead\n"
    part_name = "customer_batch_part_001.csv"
    path = tmp_path / f"{request.id}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(part_name, csv_contents)
    contents = path.read_bytes()
    artifact = BatchArtifact(
        request_id=request.id,
        path=str(path),
        filename="customer_batch.zip",
        row_count=1,
        parts=[{"filename": part_name, "row_count": 1}],
        byte_count=len(contents),
        sha256="a" * 64,
        delivery_status="sent",
        expires_at=expires_at,
    )
    session.add(artifact)
    session.flush()
    return artifact, contents


def _states(graph) -> dict[str, str]:
    return {node.key: node.state for node in graph.milestones}


# --- The milestone graph ----------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            RequestStatus.pending.value,
            {
                "submitted": "current",
                "under_review": "upcoming",
                "preparing_batch": "upcoming",
                "delivered": "upcoming",
            },
        ),
        (
            RequestStatus.approved.value,
            {
                "submitted": "complete",
                "under_review": "current",
                "preparing_batch": "upcoming",
                "delivered": "upcoming",
            },
        ),
        (
            RequestStatus.processing.value,
            {
                "submitted": "complete",
                "under_review": "complete",
                "preparing_batch": "current",
                "delivered": "upcoming",
            },
        ),
        (
            RequestStatus.generated.value,
            {
                "submitted": "complete",
                "under_review": "complete",
                "preparing_batch": "current",
                "delivered": "upcoming",
            },
        ),
        (
            RequestStatus.waiting_inventory.value,
            {
                "submitted": "complete",
                "under_review": "complete",
                "preparing_batch": "paused",
                "delivered": "upcoming",
            },
        ),
        (
            RequestStatus.delivered.value,
            {
                "submitted": "complete",
                "under_review": "complete",
                "preparing_batch": "complete",
                "delivered": "complete",
            },
        ),
    ],
)
def test_graph_places_the_story_on_the_furthest_milestone_reached(
    session,
    status,
    expected,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    approved = SUBMITTED_AT + timedelta(hours=1)
    item = _request(
        session,
        user_id,
        customer,
        status=status,
        approved_at=(
            approved if status != RequestStatus.pending.value else None
        ),
        processed_at=(
            approved + timedelta(hours=1)
            if status == RequestStatus.delivered.value
            else None
        ),
        delivered_at=(
            approved + timedelta(hours=2)
            if status == RequestStatus.delivered.value
            else None
        ),
    )

    graph = build_milestones(item)

    assert _states(graph) == expected
    assert graph.outcome is None
    assert graph.milestones[0].occurred_at == SUBMITTED_AT


def test_every_milestone_the_request_reached_carries_its_timestamp(session):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    approved = SUBMITTED_AT + timedelta(hours=1)
    processed = SUBMITTED_AT + timedelta(hours=2)
    delivered = SUBMITTED_AT + timedelta(hours=3)
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.delivered.value,
        approved_at=approved,
        processed_at=processed,
        delivered_at=delivered,
    )

    stamps = {node.key: node.occurred_at for node in build_milestones(item).milestones}

    assert stamps == {
        "submitted": SUBMITTED_AT,
        "under_review": approved,
        "preparing_batch": processed,
        "delivered": delivered,
    }


def test_waiting_for_inventory_is_an_explained_pause_not_a_failure(session):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.waiting_inventory.value,
        approved_at=SUBMITTED_AT + timedelta(hours=1),
    )

    graph = build_milestones(item)

    assert graph.outcome is None
    assert graph.current_key == "preparing_batch"
    assert graph.pause is not None
    assert graph.pause.kind == "inventory_wait"
    assert graph.pause.milestone_key == "preparing_batch"
    assert "nothing you need to do" in graph.pause.description
    paused = next(
        node for node in graph.milestones if node.key == "preparing_batch"
    )
    assert paused.state == "paused"
    assert paused.description == graph.pause.description


@pytest.mark.parametrize(
    ("status", "kind", "label", "tone"),
    [
        (RequestStatus.rejected.value, "rejected", "Not Approved", "danger"),
        (RequestStatus.canceled.value, "canceled", "Canceled", "neutral"),
        (RequestStatus.failed.value, "failed", "Needs Attention", "danger"),
    ],
)
def test_a_stopped_request_names_its_own_outcome(
    session,
    status,
    kind,
    label,
    tone,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    closed = SUBMITTED_AT + timedelta(hours=4)
    item = _request(
        session,
        user_id,
        customer,
        status=status,
        approved_at=SUBMITTED_AT + timedelta(hours=1),
        closed_at=closed,
    )

    graph = build_milestones(item)

    assert graph.current_key is None
    assert graph.pause is None
    assert graph.outcome is not None
    assert (graph.outcome.kind, graph.outcome.label, graph.outcome.tone) == (
        kind,
        label,
        tone,
    )
    assert graph.outcome.occurred_at == closed
    assert graph.outcome.milestone_key == "under_review"
    assert _states(graph) == {
        "submitted": "complete",
        "under_review": "stopped",
        "preparing_batch": "not_reached",
        "delivered": "not_reached",
    }


def test_a_stopped_request_never_marks_a_later_milestone_upcoming(session):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.failed.value,
        approved_at=SUBMITTED_AT + timedelta(hours=1),
        processed_at=SUBMITTED_AT + timedelta(hours=2),
    )

    graph = build_milestones(item)

    assert _states(graph) == {
        "submitted": "complete",
        "under_review": "complete",
        "preparing_batch": "stopped",
        "delivered": "not_reached",
    }
    assert "upcoming" not in set(_states(graph).values())


# --- The workspace read -----------------------------------------------------


def test_workspace_publishes_the_bounds_the_stages_validate_against(session):
    user_id, _, _ = _customer(session, licensed_states=["tx", "FL"])
    client = _authenticate(session, user_id)

    body = client.get("/api/me/batch-requests").json()

    assert body["limits"] == {
        "minimum_lead_count": 1,
        "maximum_lead_count": 100_000,
        "licensed_states": ["FL", "TX"],
    }
    assert body["blocker"] is None
    assert body["requests"] == []


@pytest.mark.parametrize(
    ("licensed_states", "confirmed", "reason", "action_kind"),
    [
        ([], True, "no_licensed_states", "add_licensed_states"),
        (["TX"], False, "mapping_unconfirmed", "review_account"),
    ],
)
def test_workspace_blocks_the_flow_with_the_fix_rather_than_the_stages(
    session,
    licensed_states,
    confirmed,
    reason,
    action_kind,
):
    user_id, _, _ = _customer(
        session,
        licensed_states=licensed_states,
        confirmed=confirmed,
    )
    client = _authenticate(session, user_id)

    blocker = client.get("/api/me/batch-requests").json()["blocker"]

    assert blocker["reason"] == reason
    assert blocker["action"]["kind"] == action_kind
    assert blocker["action"]["href"] == "/app/account"


def test_workspace_never_leaks_the_fulfillment_state_machine(session):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    _request(
        session,
        user_id,
        customer,
        status=RequestStatus.waiting_inventory.value,
        approved_at=SUBMITTED_AT,
    )
    client = _authenticate(session, user_id)

    body = client.get("/api/me/batch-requests").text

    for internal in ("waiting_inventory", "status_message", "available_count"):
        assert internal not in body


@pytest.mark.parametrize(
    ("status", "can_cancel"),
    [
        (RequestStatus.pending.value, True),
        (RequestStatus.approved.value, True),
        (RequestStatus.waiting_inventory.value, True),
        (RequestStatus.processing.value, False),
        (RequestStatus.generated.value, False),
        (RequestStatus.delivered.value, False),
        (RequestStatus.rejected.value, False),
        (RequestStatus.canceled.value, False),
        (RequestStatus.failed.value, False),
    ],
)
def test_cancellation_is_offered_only_while_the_domain_allows_it(
    session,
    status,
    can_cancel,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(session, user_id, customer, status=status)
    client = _authenticate(session, user_id)

    body = client.get("/api/me/batch-requests").json()
    assert body["requests"][0]["can_cancel"] is can_cancel

    refused = client.post(f"/api/me/batch-requests/{item.id}/cancel")
    assert refused.status_code == (200 if can_cancel else 409)


@pytest.mark.parametrize(
    ("status", "action_kind"),
    [
        (RequestStatus.delivered.value, "submit_feedback"),
        (RequestStatus.rejected.value, "request_batch"),
        (RequestStatus.canceled.value, "request_batch"),
        (RequestStatus.failed.value, "contact_support"),
        (RequestStatus.approved.value, None),
    ],
)
def test_every_finished_request_offers_a_valid_next_action(
    session,
    status,
    action_kind,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    _request(
        session,
        user_id,
        customer,
        status=status,
        delivered_at=(
            SUBMITTED_AT
            if status == RequestStatus.delivered.value
            else None
        ),
    )
    client = _authenticate(session, user_id)

    action = client.get("/api/me/batch-requests").json()["requests"][0][
        "next_action"
    ]

    if action_kind is None:
        assert action is None
    else:
        assert action["kind"] == action_kind
        assert action["href"]


def test_delivered_request_projects_only_customer_safe_artifact_metadata(
    session,
    tmp_path,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.delivered.value,
        delivered_at=SUBMITTED_AT,
    )
    _artifact(
        session,
        tmp_path,
        item,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    client = _authenticate(session, user_id)

    body = client.get("/api/me/batch-requests").json()["requests"][0]

    artifact = body["artifact"]
    assert artifact == {
        "filename": "customer_batch.zip",
        "row_count": 1,
        "parts": [
            {
                "filename": "customer_batch_part_001.csv",
                "row_count": 1,
            }
        ],
        "expires_at": artifact["expires_at"],
        "available": True,
        "download_href": f"/api/me/batch-requests/{item.id}/artifact",
    }
    assert artifact["expires_at"] is not None
    serialized = str(artifact)
    assert "path" not in serialized
    assert "sha256" not in serialized


def test_delivered_artifact_exposes_contiguous_csv_parts_with_a_last_remainder(
    session,
    settings,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.approved.value,
        lead_count=5,
        rows_per_file=2,
    )
    session.add_all(
        [
            Lead(
                phone=f"214555010{index}",
                title=f"Lead {index}",
                state="TX",
            )
            for index in range(1, 6)
        ]
    )
    allocate_request(session, item.id, settings)
    item.status = RequestStatus.delivered.value
    item.delivered_at = datetime.now(timezone.utc)
    session.commit()
    client = _authenticate(session, user_id)

    workspace = client.get("/api/me/batch-requests").json()
    artifact = workspace["requests"][0]["artifact"]
    download = client.get(artifact["download_href"])

    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")
    assert [part["row_count"] for part in artifact["parts"]] == [2, 2, 1]
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.namelist() == [
            part["filename"] for part in artifact["parts"]
        ]
        rows_by_part = [
            list(
                csv.DictReader(
                    io.StringIO(archive.read(part["filename"]).decode())
                )
            )
            for part in artifact["parts"]
        ]
    assert [len(rows) for rows in rows_by_part] == [2, 2, 1]
    assert [
        row["phone"] for rows in rows_by_part for row in rows
    ] == [f"214555010{index}" for index in range(1, 6)]


def test_default_artifact_still_contains_one_csv_part(session, settings):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.approved.value,
        lead_count=3,
    )
    session.add_all(
        [
            Lead(
                phone=f"214555020{index}",
                title=f"Default {index}",
                state="TX",
            )
            for index in range(1, 4)
        ]
    )
    allocate_request(session, item.id, settings)
    item.status = RequestStatus.delivered.value
    item.delivered_at = datetime.now(timezone.utc)
    session.commit()
    client = _authenticate(session, user_id)

    artifact = client.get("/api/me/batch-requests").json()["requests"][0][
        "artifact"
    ]
    download = client.get(artifact["download_href"])

    assert artifact["parts"] == [
        {"filename": artifact["parts"][0]["filename"], "row_count": 3}
    ]
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.namelist() == [artifact["parts"][0]["filename"]]


def test_zip_download_and_regeneration_have_one_stable_sha256(
    session,
    settings,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.approved.value,
        lead_count=3,
        rows_per_file=2,
    )
    session.add_all(
        [
            Lead(
                phone=f"214555030{index}",
                title=f"Stable {index}",
                state="TX",
            )
            for index in range(1, 4)
        ]
    )
    allocate_request(session, item.id, settings)
    item.status = RequestStatus.delivered.value
    item.delivered_at = datetime.now(timezone.utc)
    session.commit()
    client = _authenticate(session, user_id)

    first = client.get(f"/api/me/batch-requests/{item.id}/artifact")
    second = client.get(f"/api/me/batch-requests/{item.id}/artifact")
    first_hash = hashlib.sha256(first.content).hexdigest()

    assert hashlib.sha256(second.content).hexdigest() == first_hash
    artifact = session.scalar(
        select(BatchArtifact).where(BatchArtifact.request_id == item.id)
    )
    assert artifact is not None
    assert artifact.sha256 == first_hash
    with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
        assert {entry.date_time for entry in archive.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }

    artifact.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    Path(artifact.path).unlink()
    session.commit()
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    app.dependency_overrides[get_settings] = lambda: settings

    regenerated = client.post(
        f"/api/admin/requests/{item.id}/artifact/regenerate",
        json={"reason": "Customer requested the expired Batch Artifact."},
    )
    redownload = client.get(f"/api/me/batch-requests/{item.id}/artifact")

    assert regenerated.status_code == 200
    assert regenerated.json()["sha256"] == first_hash
    assert redownload.content == first.content
    assert hashlib.sha256(redownload.content).hexdigest() == first_hash


# --- Customer Batch Artifact download --------------------------------------


def test_customer_downloads_their_live_artifact_and_the_download_is_audited(
    session,
    tmp_path,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.delivered.value,
        delivered_at=SUBMITTED_AT,
    )
    artifact, contents = _artifact(
        session,
        tmp_path,
        item,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    client = _authenticate(session, user_id)

    response = client.get(f"/api/me/batch-requests/{item.id}/artifact")

    assert response.status_code == 200
    assert response.content == contents
    assert response.headers["content-type"].startswith("application/zip")
    assert "customer_batch.zip" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    audit = session.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "batch_artifact_downloaded"
        )
    )
    assert audit is not None
    assert audit.target_type == "batch_request"
    assert audit.target_id == str(item.id)
    assert audit.actor_user_id == str(user_id)
    assert audit.details["artifactId"] == artifact.id
    assert audit.details["filename"] == artifact.filename


def test_customer_cannot_download_another_customers_artifact(session, tmp_path):
    owner_id, owner, _ = _customer(session, licensed_states=["TX"])
    other_id, _, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        owner_id,
        owner,
        status=RequestStatus.delivered.value,
        delivered_at=SUBMITTED_AT,
    )
    _artifact(
        session,
        tmp_path,
        item,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    response = _authenticate(session, other_id).get(
        f"/api/me/batch-requests/{item.id}/artifact"
    )

    assert response.status_code == 404
    assert session.scalar(select(func.count(AuditEntry.id))) == 0


def test_expired_artifact_is_gone_and_is_not_audited_as_a_download(
    session,
    tmp_path,
):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.delivered.value,
        delivered_at=SUBMITTED_AT,
    )
    _artifact(
        session,
        tmp_path,
        item,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    client = _authenticate(session, user_id)

    response = client.get(f"/api/me/batch-requests/{item.id}/artifact")

    assert response.status_code == 410
    assert "expired" in response.json()["detail"]
    assert session.scalar(select(func.count(AuditEntry.id))) == 0


def test_generated_artifact_is_not_downloadable_before_delivery(session, tmp_path):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.generated.value,
    )
    _artifact(
        session,
        tmp_path,
        item,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    response = _authenticate(session, user_id).get(
        f"/api/me/batch-requests/{item.id}/artifact"
    )

    assert response.status_code == 404
    assert session.scalar(select(func.count(AuditEntry.id))) == 0


def test_artifact_download_requires_authentication():
    response = TestClient(app).get(
        "/api/me/batch-requests/11111111-1111-4111-8111-111111111111/artifact"
    )

    assert response.status_code == 401


def test_customer_cannot_use_the_admin_regeneration_action(session):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.delivered.value,
        delivered_at=SUBMITTED_AT,
    )

    response = _authenticate(session, user_id).post(
        f"/api/admin/requests/{item.id}/artifact/regenerate",
        json={"reason": "Trying to bypass expiry."},
    )

    assert response.status_code == 403


# --- Submission -------------------------------------------------------------


def _payload(**overrides):
    return {
        "idempotency_key": "flow-0000-1111-2222",
        "lead_count": 500,
        "state_mode": "selected",
        "states": ["TX"],
        **overrides,
    }


def test_a_reviewed_request_lands_on_a_receipt_linked_to_the_batch_request(
    session,
):
    user_id, _, _ = _customer(session, licensed_states=["TX", "FL"])
    client = _authenticate(session, user_id)

    response = client.post("/api/me/batch-requests", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["request"]["lead_count"] == 500
    assert body["request"]["states"] == ["TX"]
    assert body["request"]["receipt_href"] == (
        f"/app/requests?request={body['request']['id']}"
    )
    assert body["request"]["milestones"]["current_key"] == "submitted"
    assert body["request"]["can_cancel"] is True


def test_omitting_rows_per_file_freezes_the_request_as_one_file(session):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    client = _authenticate(session, user_id)

    response = client.post("/api/me/batch-requests", json=_payload())

    assert response.status_code == 201
    assert response.json()["request"]["rows_per_file"] == 500
    request = session.scalar(select(LeadRequest))
    assert request is not None
    assert request.rows_per_file == 500


def test_submission_freezes_an_explicit_rows_per_file_choice(session):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    client = _authenticate(session, user_id)

    response = client.post(
        "/api/me/batch-requests",
        json=_payload(rows_per_file=200),
    )

    assert response.status_code == 201
    assert response.json()["request"]["rows_per_file"] == 200
    request = session.scalar(select(LeadRequest))
    assert request is not None
    assert request.rows_per_file == 200


@pytest.mark.parametrize("rows_per_file", [0, -1, 100_001])
def test_invalid_rows_per_file_is_refused_before_anything_is_created(
    session,
    rows_per_file,
):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    client = _authenticate(session, user_id)

    response = client.post(
        "/api/me/batch-requests",
        json=_payload(rows_per_file=rows_per_file),
    )

    assert response.status_code == 422
    assert session.scalar(select(func.count(LeadRequest.id))) == 0


def test_replaying_a_submission_key_never_creates_a_second_request(session):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    client = _authenticate(session, user_id)

    first = client.post("/api/me/batch-requests", json=_payload())
    second = client.post("/api/me/batch-requests", json=_payload())

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["request"]["id"] == first.json()["request"]["id"]
    assert session.scalar(select(func.count(LeadRequest.id))) == 1
    assert (
        session.scalar(
            select(func.count(Job.id)).where(Job.kind == "notify_request")
        )
        == 1
    )


def test_a_replay_is_not_a_way_to_change_the_request_it_returns(session):
    user_id, _, _ = _customer(session, licensed_states=["TX", "FL"])
    client = _authenticate(session, user_id)
    client.post("/api/me/batch-requests", json=_payload())

    replay = client.post(
        "/api/me/batch-requests",
        json=_payload(
            lead_count=99_000,
            rows_per_file=10_000,
            states=["FL"],
        ),
    )

    assert replay.status_code == 200
    assert replay.json()["request"]["lead_count"] == 500
    assert replay.json()["request"]["rows_per_file"] == 500
    assert replay.json()["request"]["states"] == ["TX"]


def test_the_database_itself_refuses_a_second_request_for_one_key(session):
    """Idempotency does not rest on the read that precedes the insert."""

    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    for _ in range(2):
        session.add(
            LeadRequest(
                user_id=user_id,
                agent=customer,
                lead_count=500,
                state_mode="all_saved",
                states_snapshot=["TX"],
                delivery_email="customer@example.com",
                status=RequestStatus.pending.value,
                idempotency_key="flow-0000-1111-2222",
            )
        )

    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_a_new_flow_key_starts_a_new_request(session):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    client = _authenticate(session, user_id)

    client.post("/api/me/batch-requests", json=_payload())
    client.post(
        "/api/me/batch-requests",
        json=_payload(idempotency_key="flow-3333-4444-5555"),
    )

    assert session.scalar(select(func.count(LeadRequest.id))) == 2


def test_two_customers_may_reuse_the_same_submission_key(session):
    first_id, _, _ = _customer(session, licensed_states=["TX"])
    second_id = uuid.uuid4()
    other = Agent(slug=f"requests-{second_id}", name="Other", licensed_states=["TX"])
    session.add_all(
        [
            other,
            CustomerProfile(
                user_id=second_id,
                email="other@example.com",
                licensed_states=["TX"],
                agent=other,
                mapping_confirmed_at=datetime.now(timezone.utc),
            ),
        ]
    )
    session.flush()

    assert (
        _authenticate(session, first_id)
        .post("/api/me/batch-requests", json=_payload())
        .status_code
        == 201
    )
    assert (
        _authenticate(session, second_id)
        .post("/api/me/batch-requests", json=_payload())
        .status_code
        == 201
    )
    assert session.scalar(select(func.count(LeadRequest.id))) == 2


@pytest.mark.parametrize("lead_count", [0, -5, 100_001])
def test_an_out_of_range_quantity_is_refused_before_anything_is_created(
    session,
    lead_count,
):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    client = _authenticate(session, user_id)

    response = client.post(
        "/api/me/batch-requests",
        json=_payload(lead_count=lead_count),
    )

    assert response.status_code == 422
    assert session.scalar(select(func.count(LeadRequest.id))) == 0


def test_an_unlicensed_state_is_refused_and_named(session):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    client = _authenticate(session, user_id)

    response = client.post(
        "/api/me/batch-requests",
        json=_payload(states=["TX", "FL"]),
    )

    assert response.status_code == 422
    assert "FL" in response.json()["detail"]
    assert session.scalar(select(func.count(LeadRequest.id))) == 0


def test_an_empty_selected_scope_is_refused(session):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    client = _authenticate(session, user_id)

    response = client.post("/api/me/batch-requests", json=_payload(states=[]))

    assert response.status_code == 422
    assert session.scalar(select(func.count(LeadRequest.id))) == 0


def test_all_saved_scope_snapshots_every_licensed_state(session):
    user_id, _, _ = _customer(session, licensed_states=["TX", "FL"])
    client = _authenticate(session, user_id)

    body = client.post(
        "/api/me/batch-requests",
        json=_payload(state_mode="all_saved", states=[]),
    ).json()

    assert body["request"]["states"] == ["FL", "TX"]


@pytest.mark.parametrize(
    ("licensed_states", "confirmed"),
    [([], True), (["TX"], False)],
)
def test_a_blocked_customer_cannot_submit_at_all(
    session,
    licensed_states,
    confirmed,
):
    user_id, _, _ = _customer(
        session,
        licensed_states=licensed_states,
        confirmed=confirmed,
    )
    client = _authenticate(session, user_id)

    response = client.post("/api/me/batch-requests", json=_payload())

    assert response.status_code == 409
    assert session.scalar(select(func.count(LeadRequest.id))) == 0


def test_an_inactive_agency_cannot_submit(session):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    customer.agency = Agency(slug="closed", name="Closed Agency", active=False)
    session.flush()
    client = _authenticate(session, user_id)

    assert (
        client.post("/api/me/batch-requests", json=_payload()).status_code
        == 409
    )


def test_a_replaced_user_account_is_refused_before_it_can_submit(session):
    user_id, _, _ = _customer(session, licensed_states=["TX"])
    session.scalar(
        select(UserAccount).where(UserAccount.auth_user_id == user_id)
    ).active = False
    session.flush()
    client = _authenticate(session, user_id)

    assert client.get("/api/me/batch-requests").status_code == 403
    assert (
        client.post("/api/me/batch-requests", json=_payload()).status_code
        == 403
    )


# --- Cancellation -----------------------------------------------------------


def test_cancelling_updates_the_returned_timeline_immediately(session):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.approved.value,
        approved_at=SUBMITTED_AT + timedelta(hours=1),
    )
    client = _authenticate(session, user_id)

    body = client.post(f"/api/me/batch-requests/{item.id}/cancel").json()

    assert body["can_cancel"] is False
    assert body["milestones"]["outcome"]["kind"] == "canceled"
    assert body["milestones"]["outcome"]["occurred_at"] is not None
    assert body["milestones"]["current_key"] is None
    assert body["next_action"]["kind"] == "request_batch"
    assert body["status"]["label"] == "Canceled"


def test_cancelling_twice_is_refused_with_a_specific_reason(session):
    user_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(session, user_id, customer, status=RequestStatus.pending.value)
    client = _authenticate(session, user_id)

    assert client.post(f"/api/me/batch-requests/{item.id}/cancel").status_code == 200
    second = client.post(f"/api/me/batch-requests/{item.id}/cancel")

    assert second.status_code == 409
    assert "no longer be" in second.json()["detail"]


def test_a_customer_cannot_cancel_another_customers_request(session):
    owner_id, customer, _ = _customer(session, licensed_states=["TX"])
    item = _request(session, owner_id, customer, status=RequestStatus.pending.value)
    intruder_id, _, _ = _customer(session, licensed_states=["TX"])

    response = _authenticate(session, intruder_id).post(
        f"/api/me/batch-requests/{item.id}/cancel"
    )

    assert response.status_code == 404
    assert session.get(LeadRequest, item.id).status == RequestStatus.pending.value
