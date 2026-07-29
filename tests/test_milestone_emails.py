from __future__ import annotations

import pytest
from sqlalchemy import func, select

from jawnix.allocation import allocate_request
from jawnix.delivery import mark_delivery_failed
from jawnix.milestone_emails import (
    MILESTONE_EMAIL_JOB,
    enqueue_milestone_email,
    send_milestone_email,
)
from jawnix.models import Agent, Job, RequestStatus
from jawnix.transitions import transition_request

from conftest import make_request


class Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.mark.parametrize(
    ("milestone", "subject_phrase", "body_phrase"),
    [
        ("approval", "approved", "checking eligible inventory"),
        (
            "waiting_inventory",
            "waiting for inventory",
            "nothing you need to do",
        ),
        ("rejection", "not approved", "no Leads were reserved"),
        ("failure", "needs attention", "Do not submit a duplicate"),
    ],
)
def test_milestone_email_content_links_to_the_authenticated_timeline(
    session,
    settings,
    monkeypatch,
    milestone,
    subject_phrase,
    body_phrase,
):
    settings.resend_api_key = "milestone-key"
    settings.public_base_url = "https://app.jawnix.example/"
    customer = Agent(slug=f"email-{milestone}", name="Email Customer")
    session.add(customer)
    session.flush()
    request = make_request(
        session,
        customer,
        1_250,
        states=["TX", "FL"],
    )
    request.delivery_email = "customer@example.com"
    calls: list[dict] = []

    def capture(*_args, **kwargs):
        calls.append(kwargs)
        return Response(200, {"id": "message-123"})

    monkeypatch.setattr("jawnix.milestone_emails.httpx.post", capture)

    assert (
        send_milestone_email(request, milestone, settings)
        == "message-123"
    )
    assert send_milestone_email(request, milestone, settings) == "message-123"

    assert len(calls) == 2
    first, repeated = calls
    payload = first["json"]
    timeline = (
        f"https://app.jawnix.example/app/requests?request={request.id}"
    )
    assert subject_phrase in payload["subject"]
    assert body_phrase in payload["text"]
    assert f"Batch Request: {request.id}" in payload["text"]
    assert "Quantity: 1,250 Leads" in payload["text"]
    assert "Licensed States: FL, TX" in payload["text"]
    assert timeline in payload["text"]
    assert "customer@example.com" not in timeline
    assert payload["to"] == ["customer@example.com"]
    assert first["headers"]["Idempotency-Key"] == (
        f"jawnix-request/{request.id}/{milestone}"
    )
    assert repeated["headers"]["Idempotency-Key"] == (
        first["headers"]["Idempotency-Key"]
    )


@pytest.mark.parametrize(
    ("status", "milestone"),
    [
        (RequestStatus.approved.value, "approval"),
        (RequestStatus.waiting_inventory.value, "waiting_inventory"),
        (RequestStatus.rejected.value, "rejection"),
        (RequestStatus.failed.value, "failure"),
    ],
)
def test_repeated_state_processing_queues_one_milestone_job(
    session,
    status,
    milestone,
):
    customer = Agent(slug=f"queue-{milestone}", name="Queue Customer")
    session.add(customer)
    session.flush()
    request = make_request(session, customer, 10)
    request.status = status

    first = enqueue_milestone_email(session, request)
    repeated = enqueue_milestone_email(session, request)
    session.flush()

    assert first is repeated
    assert first is not None
    assert first.payload == {"milestone": milestone}
    assert (
        session.scalar(
            select(func.count(Job.id)).where(
                Job.kind == MILESTONE_EMAIL_JOB,
                Job.request_id == request.id,
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "status",
    [
        RequestStatus.pending.value,
        RequestStatus.processing.value,
        RequestStatus.generated.value,
        RequestStatus.delivered.value,
        RequestStatus.canceled.value,
    ],
)
def test_internal_and_non_email_states_queue_nothing(session, status):
    customer = Agent(
        slug=f"silent-{status}",
        name="Silent Customer",
    )
    session.add(customer)
    session.flush()
    request = make_request(session, customer, 10)
    request.status = status

    assert enqueue_milestone_email(session, request) is None
    assert (
        session.scalar(
            select(func.count(Job.id)).where(
                Job.kind == MILESTONE_EMAIL_JOB,
                Job.request_id == request.id,
            )
        )
        == 0
    )


@pytest.mark.parametrize(
    ("action", "milestone"),
    [
        ("approve", "approval"),
        ("reject", "rejection"),
    ],
)
def test_operator_transitions_queue_the_customer_milestone(
    session,
    action,
    milestone,
):
    customer = Agent(
        slug=f"transition-{action}",
        name="Transition Customer",
    )
    session.add(customer)
    session.flush()
    request = make_request(session, customer, 10)
    request.status = RequestStatus.pending.value

    transition_request(
        session,
        request.id,
        action,
        actor_id="administrator",
        reason=f"Test {action}",
    )

    job = session.scalar(
        select(Job).where(
            Job.kind == MILESTONE_EMAIL_JOB,
            Job.request_id == request.id,
        )
    )
    assert job is not None
    assert job.payload == {"milestone": milestone}


def test_inventory_wait_queues_the_customer_milestone(
    session,
    settings,
):
    customer = Agent(slug="waiting-email", name="Waiting Customer")
    session.add(customer)
    session.flush()
    request = make_request(session, customer, 10)

    result = allocate_request(session, request.id, settings)

    assert result.status == RequestStatus.waiting_inventory.value
    job = session.scalar(
        select(Job).where(
            Job.kind == MILESTONE_EMAIL_JOB,
            Job.request_id == request.id,
        )
    )
    assert job is not None
    assert job.payload == {"milestone": "waiting_inventory"}


def test_delivery_failure_queues_one_failure_milestone(session):
    customer = Agent(slug="failed-email", name="Failed Customer")
    session.add(customer)
    session.flush()
    request = make_request(session, customer, 10)

    mark_delivery_failed(session, request.id, "provider unavailable")
    mark_delivery_failed(session, request.id, "provider unavailable")

    jobs = list(
        session.scalars(
            select(Job).where(
                Job.kind == MILESTONE_EMAIL_JOB,
                Job.request_id == request.id,
            )
        )
    )
    assert request.status == RequestStatus.failed.value
    assert [job.payload for job in jobs] == [{"milestone": "failure"}]


def test_provider_error_is_safe_and_keeps_request_state(
    session,
    settings,
    monkeypatch,
):
    settings.resend_api_key = "milestone-key"
    customer = Agent(slug="safe-error", name="Safe Error Customer")
    session.add(customer)
    session.flush()
    request = make_request(session, customer, 10)
    original_status = request.status
    monkeypatch.setattr(
        "jawnix.milestone_emails.httpx.post",
        lambda *_args, **_kwargs: Response(503),
    )

    with pytest.raises(RuntimeError, match="HTTP 503"):
        send_milestone_email(request, "approval", settings)

    assert request.status == original_status
    assert request.delivery_email not in str(request.status_message)
