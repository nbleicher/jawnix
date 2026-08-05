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


def _seed_notify_request_job(factory, *, attempts: int = 1):
    with factory.begin() as session:
        customer = Agent(slug="notify-retry", name="Notify Retry")
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=customer,
            lead_count=5,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email="notify-retry@example.com",
            status="pending",
        )
        session.add_all([customer, request])
        session.flush()
        job = Job(
            kind="notify_request",
            request_id=request.id,
            status=JobStatus.running.value,
            attempts=attempts,
            locked_by="worker-test",
            locked_at=datetime.now(timezone.utc),
        )
        session.add(job)
        session.flush()
        return job.id, request.id


def test_transient_notify_request_requeues_then_succeeds(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'notify-retry.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    job_id, request_id = _seed_notify_request_job(factory, attempts=1)

    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="99",
    )
    calls = {"n": 0}

    def flaky_post(_self, request):
        calls["n"] += 1
        if calls["n"] == 1:
            from jawnix.telegram import TelegramTransientError

            raise TelegramTransientError("Telegram sendMessage failed: 503")
        return "99", "msg-1"

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_request",
        flaky_post,
    )

    before = datetime.now(timezone.utc)
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.queued.value
        assert "503" in job.last_error
        run_after = job.run_after
        if run_after.tzinfo is None:
            run_after = run_after.replace(tzinfo=timezone.utc)
        assert run_after >= before + timedelta(seconds=30)
        assert job.locked_by == ""
        assert job.locked_at is None
        assert session.scalar(
            select(Job).where(Job.kind == "notify_telegram_action_failure")
        ) is None

    process_job(job_id)

    with factory() as session:
        from jawnix.models import Notification

        job = session.get(Job, job_id)
        notification = session.scalar(
            select(Notification).where(Notification.request_id == request_id)
        )
        assert job.status == JobStatus.complete.value
        assert job.last_error == ""
        assert notification is not None
        assert notification.message_id == "msg-1"
    engine.dispose()


def test_notify_request_honors_telegram_retry_after(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'notify-429.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    job_id, _request_id = _seed_notify_request_job(factory, attempts=1)

    settings = Settings(TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="99")

    def rate_limited(_self, request):
        from jawnix.telegram import TelegramTransientError

        raise TelegramTransientError(
            "Too Many Requests",
            retry_after=timedelta(seconds=42),
        )

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_request",
        rate_limited,
    )

    before = datetime.now(timezone.utc)
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.queued.value
        run_after = job.run_after
        if run_after.tzinfo is None:
            run_after = run_after.replace(tzinfo=timezone.utc)
        assert run_after >= before + timedelta(seconds=42)
        assert run_after < before + timedelta(seconds=60)
    engine.dispose()


def test_permanent_notify_failure_alerts_once(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'notify-permanent.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    job_id, request_id = _seed_notify_request_job(factory, attempts=1)

    settings = Settings(TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="99")

    def permanent(_self, request):
        raise RuntimeError("Telegram sendMessage failed: chat not found")

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_request",
        permanent,
    )

    process_job(job_id)
    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        notices = list(
            session.scalars(
                select(Job).where(Job.kind == "notify_telegram_action_failure")
            )
        )
        assert job.status == JobStatus.failed.value
        assert len(notices) == 1
        message = notices[0].payload["message"]
        assert str(request_id) in message
        assert "re-notify" in message.lower()
        assert job.payload.get("failure_notification_job_id") == notices[0].id
    engine.dispose()


def test_exhausted_notify_attempts_fail_and_alert(tmp_path, monkeypatch):
    from jawnix.telegram import TELEGRAM_NOTIFY_MAX_ATTEMPTS

    engine = create_engine(f"sqlite:///{tmp_path / 'notify-exhausted.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    job_id, _request_id = _seed_notify_request_job(
        factory, attempts=TELEGRAM_NOTIFY_MAX_ATTEMPTS
    )

    settings = Settings(TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="99")

    def still_down(_self, request):
        from jawnix.telegram import TelegramTransientError

        raise TelegramTransientError("Telegram sendMessage failed: 503")

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_request",
        still_down,
    )

    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        notices = list(
            session.scalars(
                select(Job).where(Job.kind == "notify_telegram_action_failure")
            )
        )
        assert job.status == JobStatus.failed.value
        assert len(notices) == 1
    engine.dispose()


def test_edit_target_missing_reposts_and_rewrites_notification(
    tmp_path, monkeypatch
):
    from jawnix.models import Notification
    from jawnix.telegram import TelegramEditTargetMissingError

    engine = create_engine(f"sqlite:///{tmp_path / 'notify-repost.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        customer = Agent(slug="notify-repost", name="Notify Repost")
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent=customer,
            lead_count=5,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email="notify-repost@example.com",
            status="approved",
        )
        session.add_all([customer, request])
        session.flush()
        session.add(
            Notification(
                request_id=request.id,
                destination_id="old-chat",
                message_id="old-msg",
            )
        )
        job = Job(
            kind="update_notification",
            request_id=request.id,
            status=JobStatus.running.value,
            attempts=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        request_id = request.id
        notification_id = session.scalar(
            select(Notification.id).where(Notification.request_id == request.id)
        )

    settings = Settings(TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="99")

    def missing_edit(_self, request, chat_id, message_id):
        raise TelegramEditTargetMissingError("message to edit not found")

    def fresh_post(_self, request):
        return "new-chat", "new-msg"

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.update_request",
        missing_edit,
    )
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_request",
        fresh_post,
    )

    process_job(job_id)

    with factory() as session:
        job = session.get(Job, job_id)
        notification = session.get(Notification, notification_id)
        assert job.status == JobStatus.complete.value
        assert notification.destination_id == "new-chat"
        assert notification.message_id == "new-msg"
        # Still one row (unique per request) — rewritten in place.
        assert session.scalar(
            select(Notification).where(Notification.request_id == request_id)
        ).id == notification_id
    engine.dispose()
