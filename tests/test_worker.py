from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from jawnix.config import Settings
from jawnix.database import Base
from jawnix.milestone_emails import MILESTONE_EMAIL_JOB
from jawnix.models import (
    Agent,
    AuditEntry,
    BatchHold,
    ExclusionList,
    Job,
    JobStatus,
    LeadRequest,
    NightlyReview,
    ScraperRun,
)
from jawnix.worker import process_job


class EmailResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict | None = None,
    ):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_telegram_request_decision_uses_shared_activity_record(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        customer = Agent(
            slug="telegram-audit-customer",
            name="Telegram Audit Customer",
        )
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=customer,
            lead_count=10,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email="telegram-audit@example.com",
            status="pending",
        )
        session.add_all([customer, request])
        session.flush()
        job = Job(
            kind="telegram_action",
            request_id=request.id,
            payload={
                "action": "approve",
                "approver_user_id": "12345",
            },
            status=JobStatus.running.value,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        request_id = request.id

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    process_job(job_id)

    with factory() as session:
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "batch_request_approve"
            )
        )
        assert audit is not None
        assert audit.target_type == "batch_request"
        assert audit.target_id == str(request_id)
        assert audit.actor_user_id == "telegram:12345"
        assert audit.reason == "Telegram Batch Request decision"
        assert audit.details == {
            "before": {"status": "pending"},
            "after": {"status": "approved"},
        }
    engine.dispose()


def test_failed_allocation_releases_the_active_batch_hold(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'failed-allocation.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        customer = Agent(
            slug="failed-billed-allocation",
            name="Failed billed allocation",
            billing_enabled=True,
            lead_rate_cents_per_thousand=1_000,
        )
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=customer,
            lead_count=10,
            states_snapshot=["TX"],
            delivery_email="failure@example.com",
            status="approved",
            is_billed=True,
            lead_rate_cents_per_thousand=1_000,
            billing_amount_cents=10,
        )
        session.add_all([customer, request])
        session.flush()
        hold = BatchHold(
            request_id=request.id,
            customer_id=customer.id,
            amount_cents=10,
            status="active",
        )
        job = Job(
            kind="allocate_request",
            request_id=request.id,
            status=JobStatus.running.value,
        )
        session.add_all([hold, job])
        session.flush()
        job_id = job.id
        request_id = request.id

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr(
        "jawnix.worker.allocate_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated allocation failure")
        ),
    )
    process_job(job_id)

    with factory() as session:
        request = session.get(LeadRequest, request_id)
        hold = session.scalar(
            select(BatchHold).where(BatchHold.request_id == request_id)
        )
        assert request.status == "failed"
        assert hold.status == "released"
        assert hold.released_at is not None
    engine.dispose()


def test_transient_ingestion_failures_are_retried(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'ingestion-retry.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        job = Job(
            kind="ingest_exclusion_list",
            payload={"exclusion_list_id": str(uuid.uuid4())},
            status=JobStatus.running.value,
            attempts=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr(
        "jawnix.exclusions.ingest_exclusion_list",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("temporary database failure")
        ),
    )
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.queued.value
        assert job.locked_at is None
        assert job.run_after > job.created_at
        assert "temporary database failure" in job.last_error
    engine.dispose()


def test_exhausted_ingestion_retries_mark_the_list_failed(tmp_path, monkeypatch):
    """The uploader watches the list, not the Job, so the list must move."""

    engine = create_engine(f"sqlite:///{tmp_path / 'ingestion-exhausted.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        customer = Agent(slug="exhausted-upload", name="Exhausted upload")
        session.add(customer)
        session.flush()
        item = ExclusionList(
            customer_id=customer.id,
            uploaded_by="customer:exhausted",
            exclusion_type="dnc",
            filename="dnc.csv",
            storage_path=str(tmp_path / "dnc.csv"),
            status="queued",
        )
        session.add(item)
        session.flush()
        job = Job(
            kind="ingest_exclusion_list",
            payload={"exclusion_list_id": str(item.id)},
            status=JobStatus.running.value,
            attempts=3,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        list_id = item.id

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr(
        "jawnix.exclusions.ingest_exclusion_list",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("permanent database failure")
        ),
    )
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        item = session.get(ExclusionList, list_id)
        assert job.status == JobStatus.failed.value
        assert item.status == "failed"
        assert "again" in item.error
    engine.dispose()


def test_milestone_job_sends_once_when_processing_is_repeated(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'milestone-worker.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        customer = Agent(
            slug="milestone-worker",
            name="Milestone Worker Customer",
        )
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=customer,
            lead_count=25,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email="milestone@example.com",
            status="approved",
        )
        session.add_all([customer, request])
        session.flush()
        job = Job(
            kind=MILESTONE_EMAIL_JOB,
            request_id=request.id,
            payload={"milestone": "approval"},
            status=JobStatus.running.value,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    settings = Settings(
        RESEND_API_KEY="milestone-key",
        JAWNIX_PUBLIC_BASE_URL="https://app.jawnix.example",
    )
    sends = 0

    def send(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        return EmailResponse(200, {"id": "milestone-message"})

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr("jawnix.milestone_emails.httpx.post", send)

    process_job(job_id)
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        request = session.get(LeadRequest, job.request_id)
        assert sends == 1
        assert job.status == JobStatus.complete.value
        assert job.payload == {
            "milestone": "approval",
            "message_id": "milestone-message",
        }
        assert request.status == "approved"
    engine.dispose()


def test_milestone_provider_failure_fails_only_the_job(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'milestone-failure.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        customer = Agent(
            slug="milestone-failure",
            name="Milestone Failure Customer",
        )
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=customer,
            lead_count=25,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email="milestone@example.com",
            status="approved",
        )
        session.add_all([customer, request])
        session.flush()
        job = Job(
            kind=MILESTONE_EMAIL_JOB,
            request_id=request.id,
            payload={"milestone": "approval"},
            status=JobStatus.running.value,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        request_id = request.id

    settings = Settings(RESEND_API_KEY="milestone-key")
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.milestone_emails.httpx.post",
        lambda *_args, **_kwargs: EmailResponse(503),
    )

    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        request = session.get(LeadRequest, request_id)
        assert job.status == JobStatus.failed.value
        assert "HTTP 503" in job.last_error
        assert request.status == "approved"
        assert request.status_message == ""
    engine.dispose()


def test_nightly_review_delivery_commits_message_link_once(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        run = ScraperRun(
            source="google_maps",
            source_version="successful-delivery",
            status="complete",
            details={},
        )
        session.add(run)
        session.flush()
        review = NightlyReview(
            scraper_run_id=run.id,
            summary={"run": {"id": run.id}},
        )
        session.add(review)
        session.flush()
        job = Job(
            kind="notify_nightly_review",
            payload={"review_id": str(review.id)},
            status=JobStatus.running.value,
        )
        session.add(job)
        session.flush()
        review_id = review.id
        job_id = job.id

    sends = 0

    def deliver(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        return "4321"

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_nightly_review",
        deliver,
    )

    process_job(job_id)
    process_job(job_id)

    with factory() as session:
        review = session.get(NightlyReview, review_id)
        job = session.get(Job, job_id)
        assert sends == 1
        assert review.telegram_delivery_state == "sent"
        assert review.telegram_message_id == "4321"
        assert job.status == JobStatus.complete.value
    engine.dispose()


def test_nightly_review_crash_never_automatically_sends_twice(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        run = ScraperRun(
            source="google_maps",
            source_version="crash-acceptance",
            status="complete",
            details={},
        )
        session.add(run)
        session.flush()
        review = NightlyReview(
            scraper_run_id=run.id,
            summary={"run": {"id": run.id}},
        )
        session.add(review)
        session.flush()
        job = Job(
            kind="notify_nightly_review",
            payload={"review_id": str(review.id)},
            status=JobStatus.running.value,
            attempts=1,
            locked_at=datetime.now(timezone.utc),
            locked_by="worker-before-crash",
        )
        session.add(job)
        session.flush()
        review_id = review.id
        job_id = job.id

    sends = 0

    def accepted_then_process_dies(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        raise KeyboardInterrupt("simulated crash after Telegram accepted")

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_nightly_review",
        accepted_then_process_dies,
    )

    with pytest.raises(KeyboardInterrupt, match="simulated crash"):
        process_job(job_id)

    with factory.begin() as session:
        review = session.get(NightlyReview, review_id)
        job = session.get(Job, job_id)
        assert review.telegram_delivery_state == "sending"
        job.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
        job.locked_by = "worker-after-crash"
        job.attempts += 1

    process_job(job_id)

    with factory() as session:
        review = session.get(NightlyReview, review_id)
        job = session.get(Job, job_id)
        assert sends == 1
        assert review.telegram_delivery_state == "unknown"
        assert review.telegram_message_id == ""
        assert job.status == JobStatus.failed.value
        assert "reconciliation" in job.last_error.lower()
    engine.dispose()
