"""Batches and the batch page on the Customer side (C3).

The list and page read where each batch is in the Customer's words, what it
cost, and its history; the page adds leads per state. An expired file can be
copied again by the Customer: the ask is audited and rate limited, and the
worker rebuilds the same bytes through the operator's rebuild path.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Request as HttpRequest, Response as HttpResponse
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from jawnix.allocation import allocate_request
from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.batch_copies import (
    NEW_COPY_ACTOR,
    NEW_COPY_JOB,
    NEW_COPY_LIMIT,
    make_new_copy,
)
from jawnix.config import Settings, get_settings
from jawnix.database import Base, get_db
from jawnix.milestone_emails import (
    MILESTONE_EMAIL_JOB,
    build_milestone_message,
    send_milestone_email,
)
from jawnix.models import (
    AuditEntry,
    BatchArtifact,
    BatchHold,
    CustomerProfile,
    DistributionEvent,
    Job,
    JobStatus,
    Lead,
    LeadRequest,
    RequestStatus,
    UserAccount,
    Agent,
)
from jawnix.worker import process_job

SUBMITTED_AT = datetime(2026, 10, 1, 14, 20, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _authenticate(session, user_id: uuid.UUID, settings: Settings | None = None) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="customer@example.com",
        role="customer",
        csrf="test",
    )
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _customer(session, *, states: list[str] | None = None):
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"batches-{user_id}",
        name="Northstar Insurance",
        licensed_states=states or ["FL", "TX"],
    )
    profile = CustomerProfile(
        user_id=user_id,
        email=f"{user_id}@example.com",
        first_name="Casey",
        licensed_states=states or ["FL", "TX"],
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
    return user_id, customer


def _request(session, user_id, customer, *, status: str, lead_count: int = 3, **fields) -> LeadRequest:
    item = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=lead_count,
        rows_per_file=fields.pop("rows_per_file", lead_count),
        state_mode="all_saved",
        states_snapshot=fields.pop("states", ["FL", "TX"]),
        delivery_email="customer@example.com",
        status=status,
        created_at=SUBMITTED_AT,
        **fields,
    )
    session.add(item)
    session.flush()
    return item


def _delivered(session, settings, user_id, customer, *, rows_per_file: int = 2) -> LeadRequest:
    """A batch allocated and delivered through the real path, file and all."""

    item = _request(
        session,
        user_id,
        customer,
        status=RequestStatus.approved.value,
        rows_per_file=rows_per_file,
        approved_at=SUBMITTED_AT + timedelta(minutes=5),
    )
    session.add_all(
        [
            Lead(phone=f"30555504{index:02d}", title=f"Sunshine {index}", state="FL")
            for index in range(1, 3)
        ]
        + [Lead(phone="2145550499", title="Lone Star Plumbing", state="TX")]
    )
    allocate_request(session, item.id, settings)
    item.status = RequestStatus.delivered.value
    item.delivered_at = datetime.now(timezone.utc)
    session.commit()
    return item


def _expire(session, item: LeadRequest, *, remove_file: bool = True) -> None:
    item.artifact.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    if remove_file:
        Path(item.artifact.path).unlink()
    session.commit()


def _detail(client: TestClient, item: LeadRequest) -> dict:
    response = client.get(f"/api/me/batch-requests/{item.number}")
    assert response.status_code == 200, response.text
    return response.json()


# --- Where each batch is ------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "stage"),
    [
        (RequestStatus.pending.value, "waiting_approval"),
        (RequestStatus.approved.value, "preparing"),
        (RequestStatus.waiting_inventory.value, "preparing"),
        (RequestStatus.processing.value, "preparing"),
        (RequestStatus.generated.value, "preparing"),
        (RequestStatus.rejected.value, "not_approved"),
        (RequestStatus.canceled.value, "canceled"),
        (RequestStatus.failed.value, "needs_attention"),
        (RequestStatus.delivered.value, "delivered"),
    ],
)
def test_every_batch_reads_as_a_stage_in_customer_words(session, status, stage):
    user_id, customer = _customer(session)
    _request(session, user_id, customer, status=status)
    client = _authenticate(session, user_id)

    batch = client.get("/api/me/batch-requests").json()["requests"][0]

    assert batch["stage"] == stage
    assert batch["can_request_copy"] is False
    assert batch["href"] == f"/app/batches/{batch['number']}"


def test_a_live_file_is_ready_and_turns_red_only_when_expiring_undownloaded(session, settings):
    user_id, customer = _customer(session)
    item = _delivered(session, settings, user_id, customer)
    client = _authenticate(session, user_id)

    assert _detail(client, item)["stage"] == "ready"

    item.artifact.expires_at = datetime.now(timezone.utc) + timedelta(days=3)
    session.commit()
    assert _detail(client, item)["stage"] == "expiring"

    downloaded = client.get(f"/api/me/batch-requests/{item.id}/artifact")
    assert downloaded.status_code == 200
    # Already downloaded: nothing left to do, so the badge is not red.
    assert _detail(client, item)["stage"] == "ready"


def test_the_batch_page_reads_charge_history_files_and_leads_per_state(session, settings):
    user_id, customer = _customer(session)
    item = _delivered(session, settings, user_id, customer)
    item.is_billed = True
    item.lead_rate_cents_per_thousand = 500
    item.billing_amount_cents = 2
    captured_at = datetime.now(timezone.utc)
    session.add(
        BatchHold(
            request=item,
            customer_id=customer.id,
            amount_cents=2,
            status="captured",
            captured_at=captured_at,
        )
    )
    session.commit()
    client = _authenticate(session, user_id)

    body = _detail(client, item)

    assert body["number"] == item.number
    assert body["leads_by_state"] == [
        {"state": "FL", "name": "Florida", "lead_count": 2},
        {"state": "TX", "name": "Texas", "lead_count": 1},
    ]
    assert body["cost"]["rate_cents_per_thousand"] == 500
    assert body["cost"]["amount_cents"] == 2
    assert body["cost"]["state"] == "charged"
    assert body["artifact"]["columns"] == ["phone", "title", "state"]
    assert [part["row_count"] for part in body["artifact"]["parts"]] == [2, 1]
    assert [(event["key"], event["done"]) for event in body["history"]] == [
        ("submitted", True),
        ("approved", True),
        ("delivered", True),
    ]
    # The list does not pay for the per-state read.
    listed = client.get("/api/me/batch-requests").json()["requests"][0]
    assert listed["leads_by_state"] is None


def test_an_open_batch_shows_what_is_held_and_what_is_still_to_come(session):
    user_id, customer = _customer(session)
    item = _request(session, user_id, customer, status=RequestStatus.pending.value)
    item.is_billed = True
    item.lead_rate_cents_per_thousand = 500
    item.billing_amount_cents = 125_00
    session.add(
        BatchHold(request=item, customer_id=customer.id, amount_cents=125_00, status="active")
    )
    session.commit()
    client = _authenticate(session, user_id)

    body = _detail(client, item)

    assert body["cost"]["state"] == "held"
    assert body["leads_by_state"] is None
    assert [(event["key"], event["done"]) for event in body["history"]] == [
        ("submitted", True),
        ("approved", False),
        ("delivered", False),
    ]


def test_an_unbilled_batch_has_no_cost(session):
    user_id, customer = _customer(session)
    item = _request(session, user_id, customer, status=RequestStatus.rejected.value)
    client = _authenticate(session, user_id)

    body = _detail(client, item)

    assert body["cost"] is None
    assert [event["key"] for event in body["history"]] == ["submitted", "not_approved"]


# --- Get a new copy -------------------------------------------------------------


def _copy_jobs(session, item: LeadRequest) -> list[Job]:
    return list(
        session.scalars(
            select(Job).where(Job.kind == NEW_COPY_JOB, Job.request_id == item.id)
        )
    )


def test_asking_for_a_new_copy_queues_one_rebuild_and_is_audited(session, settings):
    user_id, customer = _customer(session)
    item = _delivered(session, settings, user_id, customer)
    _expire(session, item)
    client = _authenticate(session, user_id)

    before = _detail(client, item)
    asked = client.post(f"/api/me/batch-requests/{item.id}/new-copy")
    again = client.post(f"/api/me/batch-requests/{item.id}/new-copy")

    assert before["stage"] == "file_expired"
    assert before["can_request_copy"] is True
    assert asked.status_code == 202, asked.text
    assert asked.json()["stage"] == "copy_on_the_way"
    assert asked.json()["can_request_copy"] is False
    assert asked.json()["history"][-1] == {
        "key": "copy_on_the_way",
        "label": "New copy on the way",
        "at": None,
        "done": False,
    }
    # Asking twice while one is on the way returns the same copy.
    assert again.status_code == 200
    assert len(_copy_jobs(session, item)) == 1
    audits = list(
        session.scalars(
            select(AuditEntry).where(AuditEntry.action == "batch_artifact_copy_requested")
        )
    )
    assert len(audits) == 1
    assert audits[0].target_id == str(item.id)
    assert audits[0].actor_user_id == str(user_id)


def test_a_new_copy_is_refused_for_live_undelivered_and_foreign_batches(session, settings):
    user_id, customer = _customer(session)
    live = _delivered(session, settings, user_id, customer)
    pending = _request(session, user_id, customer, status=RequestStatus.pending.value)
    other_user, other_customer = _customer(session)
    foreign = _request(session, other_user, other_customer, status=RequestStatus.delivered.value)
    client = _authenticate(session, user_id)

    live_refusal = client.post(f"/api/me/batch-requests/{live.id}/new-copy")
    pending_refusal = client.post(f"/api/me/batch-requests/{pending.id}/new-copy")
    foreign_refusal = client.post(f"/api/me/batch-requests/{foreign.id}/new-copy")

    assert live_refusal.status_code == 409
    assert "has not expired" in live_refusal.json()["detail"]
    assert pending_refusal.status_code == 404
    assert foreign_refusal.status_code == 404
    assert session.scalar(select(func.count()).select_from(Job).where(Job.kind == NEW_COPY_JOB)) == 0


def test_a_batch_that_cannot_be_rebuilt_says_to_email_instead(session, settings):
    user_id, customer = _customer(session)
    item = _delivered(session, settings, user_id, customer)
    _expire(session, item)
    first = session.scalar(
        select(DistributionEvent).where(DistributionEvent.request_id == item.id).limit(1)
    )
    session.delete(first)
    session.commit()
    client = _authenticate(session, user_id)

    refused = client.post(f"/api/me/batch-requests/{item.id}/new-copy")

    assert refused.status_code == 409
    assert "Email noah@jawnix.com" in refused.json()["detail"]
    assert _copy_jobs(session, item) == []


def test_new_copies_are_rate_limited_per_customer(session, settings):
    user_id, customer = _customer(session)
    items = []
    for _ in range(NEW_COPY_LIMIT + 1):
        item = _request(
            session,
            user_id,
            customer,
            status=RequestStatus.delivered.value,
            lead_count=0,
            delivered_at=SUBMITTED_AT,
        )
        session.add(
            BatchArtifact(
                request_id=item.id,
                path=str(Path(settings.batch_dir) / f"{item.id}.zip"),
                filename="batch.zip",
                row_count=0,
                parts=[],
                byte_count=0,
                sha256="a" * 64,
                expires_at=SUBMITTED_AT + timedelta(days=30),
            )
        )
        items.append(item)
    session.commit()
    client = _authenticate(session, user_id)

    answers = [
        client.post(f"/api/me/batch-requests/{item.id}/new-copy").status_code
        for item in items
    ]

    assert answers == [202] * NEW_COPY_LIMIT + [429]
    refused = client.post(f"/api/me/batch-requests/{items[-1].id}/new-copy")
    assert "Try again tomorrow" in refused.json()["detail"]


def test_the_worker_rebuilds_the_same_bytes_and_emails_the_customer(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'new-copy.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    settings = Settings(
        JAWNIX_BATCH_DIR=tmp_path / "batches",
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
    )
    with factory() as session:
        user_id, customer = _customer(session)
        item = _delivered(session, settings, user_id, customer)
        original = Path(item.artifact.path).read_bytes()
        stored_hash = item.artifact.sha256
        _expire(session, item)
        client = _authenticate(session, user_id)
        assert client.post(f"/api/me/batch-requests/{item.id}/new-copy").status_code == 202
        job_id = _copy_jobs(session, item)[0].id
        request_id = item.id

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        item = session.get(LeadRequest, request_id)
        assert job.status == JobStatus.complete.value, job.last_error
        assert item.artifact.sha256 == stored_hash
        assert Path(item.artifact.path).read_bytes() == original
        assert hashlib.sha256(original).hexdigest() == stored_hash
        rebuilt = session.scalar(
            select(AuditEntry).where(AuditEntry.action == "batch_artifact_regenerated")
        )
        assert rebuilt.actor_user_id == NEW_COPY_ACTOR
        assert rebuilt.details["sha256"] == stored_hash
        email = session.scalar(
            select(Job).where(Job.kind == MILESTONE_EMAIL_JOB, Job.request_id == request_id)
        )
        assert email.payload == {
            "milestone": "new_copy",
            "idempotency_key": f"jawnix-request/{request_id}/new_copy/{job_id}",
        }
        client = _authenticate(session, user_id)
        body = _detail(client, item)
        assert body["stage"] == "ready"
        assert body["artifact"]["download_href"] is not None
        assert [event["key"] for event in body["history"]][-1] == "copy_ready"
        # Nothing to rebuild once the file is live again.
        assert make_new_copy(session, request_id, settings, job_id=job_id) is None
    engine.dispose()


def test_the_new_copy_email_names_the_batch_and_links_its_page(session, settings, monkeypatch):
    settings.resend_api_key = "copy-key"
    settings.public_base_url = "https://app.jawnix.example"
    user_id, customer = _customer(session)
    item = _request(session, user_id, customer, status=RequestStatus.delivered.value, lead_count=50_000)
    calls: list[dict] = []

    def capture(*_args, **kwargs):
        calls.append(kwargs)
        return HttpResponse(
            200,
            json={"id": "copy-message"},
            request=HttpRequest("POST", "https://api.resend.com/emails"),
        )

    monkeypatch.setattr("jawnix.milestone_emails.httpx.post", capture)

    message = build_milestone_message(item, "new_copy", settings)
    sent = send_milestone_email(item, "new_copy", settings, idempotency_key="copy/1")

    assert sent == "copy-message"
    assert message.subject == f"Your new copy of Batch #{item.number} is ready"
    assert "same 50,000 leads" in message.text
    assert f"https://app.jawnix.example/app/batches/{item.number}" in message.text
    assert calls[0]["headers"]["Idempotency-Key"] == "copy/1"


def test_an_expired_download_points_at_the_new_copy(session, settings):
    user_id, customer = _customer(session)
    item = _delivered(session, settings, user_id, customer)
    _expire(session, item, remove_file=False)
    client = _authenticate(session, user_id)

    refused = client.get(f"/api/me/batch-requests/{item.id}/artifact")

    assert refused.status_code == 410
    assert "Get a new copy" in refused.json()["detail"]


# --- Report a bad lead --------------------------------------------------------


def test_report_lookup_finds_the_lead_only_within_the_batch(session, settings):
    user_id, customer = _customer(session)
    item = _delivered(session, settings, user_id, customer)
    other = _request(session, user_id, customer, status=RequestStatus.delivered.value)
    client = _authenticate(session, user_id)

    found = client.post(
        "/api/me/feedback/lookup",
        json={"phone": "(214) 555-0499", "request_id": str(item.id)},
    )
    elsewhere = client.post(
        "/api/me/feedback/lookup",
        json={"phone": "(214) 555-0499", "request_id": str(other.id)},
    )
    report = client.post(
        f"/api/me/distributions/{found.json()['distributionEventId']}/reports",
        json={"reason": "wrong_state", "details": "Listed in Texas, based in Oklahoma."},
    )

    assert found.status_code == 200
    assert found.json()["batchId"] == str(item.id)
    assert found.json()["businessName"] == "Lone Star Plumbing"
    assert elsewhere.status_code == 404
    assert report.status_code == 201
    assert report.json()["reason"] == "wrong_state"
