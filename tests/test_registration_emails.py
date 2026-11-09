"""The person who signed up hears from us when the operator decides."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from jawnix.config import Settings
from jawnix.customer_registration import complete_registration, decide_registration
from jawnix.database import Base
from jawnix.jobs import job_lane
from jawnix.models import (
    Customer,
    CustomerHistoryIdentity,
    CustomerRegistration,
    Job,
    JobStatus,
    utcnow,
)
from jawnix.registration_emails import (
    REGISTRATION_EMAIL_JOB,
    build_registration_message,
    enqueue_registration_email,
    send_registration_email,
)
from jawnix.schemas import RegistrationDecision, SignupProfile
from jawnix.worker import process_job


class EmailResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _registration(status: str = "awaiting_review") -> CustomerRegistration:
    return CustomerRegistration(
        auth_user_id=uuid.uuid4(),
        email="river@northstar.example",
        first_name="River",
        last_name="Morgan",
        phone="6155550100",
        licensed_states=["TX", "FL"],
        status=status,
    )


def _archive(session, first="River", last="Morgan") -> Customer:
    customer = Customer(
        slug=f"archived-{uuid.uuid4().hex}",
        name=f"{first} {last}",
        active=False,
        deleted_at=utcnow(),
        licensed_states=["TN"],
    )
    session.add(customer)
    session.flush()
    session.add(CustomerHistoryIdentity(customer_id=customer.id, first_name=first, last_name=last))
    session.flush()
    return customer


def _signup(session, settings) -> CustomerRegistration:
    registration = complete_registration(
        session,
        user_id=uuid.uuid4(),
        email="river@northstar.example",
        details=SignupProfile(first_name="River", last_name="Morgan", phone="6155550100", licensed_states=["TX", "FL"]),
        settings=settings,
    )
    session.flush()
    return registration


def _emails(session) -> list[Job]:
    return list(session.scalars(select(Job).where(Job.kind == REGISTRATION_EMAIL_JOB)))


def test_approval_names_the_appointed_states_and_links_to_sign_in():
    settings = Settings(JAWNIX_PUBLIC_BASE_URL="https://app.jawnix.example/")

    message = build_registration_message(_registration("active"), settings)

    assert message.subject == "Your Jawnix account is ready"
    assert message.text.startswith("Hi River,")
    assert "Appointed states: Florida, Texas" in message.text
    assert "https://app.jawnix.example/app/home" in message.text
    assert "history" not in message.text
    assert "Licensed" not in message.text


def test_restored_approval_says_the_history_came_back():
    message = build_registration_message(_registration("active"), Settings(), restored=True)

    assert "earlier batches and billing history are in your account" in message.text


def test_rejection_says_so_and_invites_a_reply():
    message = build_registration_message(_registration("rejected"), Settings())

    assert message.subject == "Your Jawnix registration"
    assert "could not approve access" in message.text
    assert "reply to this email" in message.text
    assert "/app/home" not in message.text


def test_an_undecided_registration_has_no_email():
    with pytest.raises(ValueError):
        build_registration_message(_registration(), Settings())


@pytest.mark.parametrize(
    ("decision", "status", "restored"),
    [("restore", "active", True), ("new", "active", False), ("reject", "rejected", False)],
)
def test_every_operator_decision_queues_one_email(session, settings, decision, status, restored):
    archived = _archive(session)
    registration = _signup(session, settings)
    assert registration.status == "awaiting_review"
    assert _emails(session) == []

    decide_registration(
        session,
        registration.auth_user_id,
        RegistrationDecision(
            decision=decision,
            restore_customer_id=archived.id if decision == "restore" else None,
        ),
        "operator",
        settings,
    )
    enqueue_registration_email(session, registration, restored=restored)

    jobs = _emails(session)
    assert len(jobs) == 1
    assert jobs[0].request_id is None
    assert jobs[0].payload == {
        "user_id": str(registration.auth_user_id),
        "status": status,
        "restored": restored,
    }
    assert job_lane(REGISTRATION_EMAIL_JOB) == "notifications"


def test_a_registration_without_a_name_match_activates_without_an_email(session, settings):
    registration = _signup(session, settings)

    assert registration.status == "active"
    assert _emails(session) == []


def test_provider_failure_raises_without_leaking_the_address(monkeypatch):
    settings = Settings(RESEND_API_KEY="registration-key")
    monkeypatch.setattr(
        "jawnix.registration_emails.httpx.post",
        lambda *_args, **_kwargs: EmailResponse(503),
    )

    with pytest.raises(RuntimeError, match="HTTP 503") as raised:
        send_registration_email(_registration("active"), settings)

    assert "river@" not in str(raised.value)


def test_worker_sends_the_decision_once(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'registration-email.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        registration = _registration("active")
        session.add(registration)
        session.flush()
        job = enqueue_registration_email(session, registration, restored=True)
        job.status = JobStatus.running.value
        job_id = job.id
        user_id = registration.auth_user_id

    settings = Settings(
        RESEND_API_KEY="registration-key",
        JAWNIX_PUBLIC_BASE_URL="https://app.jawnix.example",
    )
    sent: list[dict] = []

    def send(*_args, **kwargs):
        sent.append(kwargs)
        return EmailResponse(200, {"id": "registration-message"})

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr("jawnix.registration_emails.httpx.post", send)

    process_job(job_id)
    process_job(job_id)

    assert len(sent) == 1
    assert sent[0]["json"]["to"] == ["river@northstar.example"]
    assert sent[0]["json"]["subject"] == "Your Jawnix account is ready"
    assert sent[0]["headers"]["Idempotency-Key"] == f"jawnix-registration/{user_id}/active"
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.complete.value
        assert job.payload["message_id"] == "registration-message"
    engine.dispose()
