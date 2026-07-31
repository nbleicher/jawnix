"""Cross-channel convergence contracts for Telegram and Jawnix (#69).

The browser endpoints are intentionally thin adapters over the commands used
here; their delegation is covered in the #57 and #68 API tests. These tests
exercise the harder boundary: durable Telegram jobs racing or replaying those
same commands, then appearing in the Jawnix read models and Activity.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from jawnix.acquisition import workspace as acquisition_workspace
from jawnix.config import Settings
from jawnix.customer_overview import build_customer_overview
from jawnix.database import Base
from jawnix.fulfillment import describe_request, workspace as fulfillment_workspace
from jawnix.models import (
    Agent,
    AuditEntry,
    CustomerProfile,
    Job,
    JobStatus,
    LeadRequest,
    NightlyReview,
    Notification,
    RequestStatus,
    ScrapeAnomaly,
    ScraperConfiguration,
    ScraperRun,
)
from jawnix.transitions import transition_request
from jawnix.worker import process_job
from jawnix_data.scraper import decide_scrape_anomaly


@pytest.fixture
def convergence_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'convergence.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _settings(tmp_path) -> Settings:
    return Settings(
        JAWNIX_SCRAPER_DB_PATH=tmp_path / "scraper" / "leads.db",
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_CHAT_ID="-10069",
    )


def _batch_request(factory) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    with factory.begin() as session:
        customer = Agent(
            slug=f"convergence-{user_id.hex[:8]}",
            name="Convergence Customer",
        )
        profile = CustomerProfile(
            user_id=user_id,
            email=f"{user_id}@example.com",
            first_name="Convergence",
            licensed_states=["TX"],
            agent=customer,
            mapping_confirmed_at=datetime.now(timezone.utc),
        )
        request = LeadRequest(
            user_id=user_id,
            agent=customer,
            lead_count=10,
            state_mode="all_saved",
            states_snapshot=["TX"],
            delivery_email=profile.email,
            status=RequestStatus.pending.value,
        )
        session.add_all([customer, profile, request])
        session.flush()
        session.add(
            Notification(
                request_id=request.id,
                provider="telegram",
                destination_id="-10069",
                message_id="batch-message",
            )
        )
        return request.id, user_id


def _telegram_batch_job(
    factory,
    request_id: uuid.UUID,
    *,
    action: str = "approve",
) -> int:
    with factory.begin() as session:
        job = Job(
            kind="telegram_action",
            request_id=request_id,
            payload={
                "action": action,
                "approver_user_id": "42",
            },
            status=JobStatus.running.value,
        )
        session.add(job)
        session.flush()
        return job.id


def _held_anomaly(factory, tmp_path) -> tuple[Settings, uuid.UUID]:
    settings = _settings(tmp_path)
    staged_path = (
        settings.scraper_db_path.parent / ".staging" / "held-dataset.db"
    )
    staged_path.parent.mkdir(parents=True)
    staged_path.write_bytes(b"held dataset for a convergence decision")
    checksum = hashlib.sha256(staged_path.read_bytes()).hexdigest()

    with factory.begin() as session:
        configuration = ScraperConfiguration(
            version=69,
            checksum="c" * 64,
            status="active",
            anomaly_thresholds={},
            created_by=uuid.uuid4(),
            reason="Convergence test configuration",
        )
        session.add(configuration)
        session.flush()
        run = ScraperRun(
            source="google_maps",
            source_version="convergence",
            configuration_id=configuration.id,
            staged_path=str(staged_path),
            checksum=checksum,
            status="held_anomaly",
            details={"anomalousSegments": []},
        )
        session.add(run)
        session.flush()
        anomaly = ScrapeAnomaly(
            scraper_run_id=run.id,
            configuration_id=configuration.id,
            dataset_checksum=checksum,
            status="pending",
            telegram_chat_id="-10069",
            telegram_message_id="nightly-message",
        )
        review = NightlyReview(
            scraper_run_id=run.id,
            summary={
                "run": {
                    "status": "held_anomaly",
                    "anomalyStatus": "pending",
                }
            },
            telegram_message_id="nightly-message",
            telegram_delivery_state="sent",
        )
        session.add_all([anomaly, review])
        session.flush()
        return settings, anomaly.id


def _telegram_anomaly_job(
    factory,
    anomaly_id: uuid.UUID,
    *,
    action: str,
) -> int:
    with factory.begin() as session:
        job = Job(
            kind="telegram_anomaly_action",
            payload={
                "action": action,
                "anomaly_id": str(anomaly_id),
                "approver_user_id": "42",
            },
            status=JobStatus.running.value,
        )
        session.add(job)
        session.flush()
        return job.id


def test_telegram_batch_action_and_replay_converge_in_every_jawnix_read(
    convergence_db,
    tmp_path,
    monkeypatch,
):
    factory = convergence_db
    settings = _settings(tmp_path)
    request_id, user_id = _batch_request(factory)
    first_job = _telegram_batch_job(factory, request_id)
    replay_job = _telegram_batch_job(factory, request_id)
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)

    process_job(first_job)
    process_job(replay_job)

    with factory() as session:
        request = session.get(LeadRequest, request_id)
        profile = session.get(CustomerProfile, user_id)
        detail = describe_request(session, request)
        fulfillment = fulfillment_workspace(session)
        overview = build_customer_overview(
            session,
            user_id=user_id,
            profile=profile,
        )

        assert request.status == RequestStatus.approved.value
        assert detail["status"] == RequestStatus.approved.value
        assert detail["history"][0]["actor"] == "telegram:42"
        assert detail["history"][0]["reason"] == (
            "Telegram Batch Request decision"
        )
        assert "approve" not in {
            action["name"] for action in detail["actions"]
        }
        assert next(
            item
            for item in fulfillment["batchRequests"]
            if item["id"] == str(request_id)
        )["status"] == RequestStatus.approved.value
        assert overview.items == []
        assert session.scalar(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.action == "batch_request_approve",
                AuditEntry.target_id == str(request_id),
            )
        ) == 1
        # The successful command and the stale replay both request a projection
        # refresh. Editing identical Telegram text is itself idempotent.
        assert session.scalar(
            select(func.count(Job.id)).where(
                Job.kind == "update_notification",
                Job.request_id == request_id,
            )
        ) == 2


def test_jawnix_batch_action_makes_a_later_telegram_click_safely_stale(
    convergence_db,
    tmp_path,
    monkeypatch,
):
    factory = convergence_db
    settings = _settings(tmp_path)
    request_id, _ = _batch_request(factory)
    with factory.begin() as session:
        transition_request(
            session,
            request_id,
            "reject",
            actor_id="admin:7",
            reason="Customer no longer needs this Batch Request.",
        )
    stale_job = _telegram_batch_job(
        factory,
        request_id,
        action="approve",
    )
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)

    process_job(stale_job)

    with factory() as session:
        request = session.get(LeadRequest, request_id)
        stale = session.get(Job, stale_job)
        assert request.status == RequestStatus.rejected.value
        assert stale.status == JobStatus.complete.value
        assert session.scalar(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.target_type == "batch_request",
                AuditEntry.target_id == str(request_id),
            )
        ) == 1
        assert session.scalar(
            select(func.count(Job.id)).where(
                Job.kind == "update_notification",
                Job.request_id == request_id,
            )
        ) == 2


def test_telegram_anomaly_action_is_immediately_visible_and_jawnix_is_stale(
    convergence_db,
    tmp_path,
    monkeypatch,
):
    factory = convergence_db
    settings, anomaly_id = _held_anomaly(factory, tmp_path)
    job_id = _telegram_anomaly_job(
        factory,
        anomaly_id,
        action="deny",
    )
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)

    process_job(job_id)
    with factory.begin() as session:
        duplicate = decide_scrape_anomaly(
            session,
            settings,
            anomaly_id,
            "confirm",
            actor_id="admin:7",
            reason="A browser decision that lost the race.",
        )

    with factory() as session:
        row = next(
            item
            for item in acquisition_workspace(session)["scrapeAnomalies"]
            if item["id"] == str(anomaly_id)
        )
        assert duplicate == {
            "status": "denied",
            "duplicate": True,
            "anomalyId": str(anomaly_id),
        }
        assert row["status"] == "denied"
        assert row["decisionBy"] == "telegram:42"
        assert row["decisionReason"] == "Telegram Scrape Anomaly decision"
        assert row["decidable"] is False
        assert session.scalar(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.action == "scrape_anomaly_denied",
                AuditEntry.target_id == str(anomaly_id),
            )
        ) == 1
        assert session.scalar(
            select(func.count(Job.id)).where(
                Job.kind == "update_nightly_review_notification",
            )
        ) == 2


def test_jawnix_anomaly_action_makes_a_later_telegram_click_safely_stale(
    convergence_db,
    tmp_path,
    monkeypatch,
):
    factory = convergence_db
    settings, anomaly_id = _held_anomaly(factory, tmp_path)
    with factory.begin() as session:
        decide_scrape_anomaly(
            session,
            settings,
            anomaly_id,
            "deny",
            actor_id="admin:7",
            reason="The source was unavailable.",
        )
    job_id = _telegram_anomaly_job(
        factory,
        anomaly_id,
        action="confirm",
    )
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)

    process_job(job_id)

    with factory() as session:
        anomaly = session.get(ScrapeAnomaly, anomaly_id)
        stale = session.get(Job, job_id)
        assert anomaly.status == "denied"
        assert anomaly.decision_by == "admin:7"
        assert stale.status == JobStatus.complete.value
        assert session.scalar(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.target_type == "scrape_anomaly",
                AuditEntry.target_id == str(anomaly_id),
            )
        ) == 1
        assert session.scalar(
            select(func.count(Job.id)).where(
                Job.kind == "update_nightly_review_notification",
            )
        ) == 2


def test_real_telegram_command_failure_queues_one_normalized_notification(
    convergence_db,
    tmp_path,
    monkeypatch,
):
    factory = convergence_db
    settings = _settings(tmp_path)
    missing_anomaly_id = uuid.uuid4()
    failed_job_id = _telegram_anomaly_job(
        factory,
        missing_anomaly_id,
        action="confirm",
    )
    sent: list[str] = []
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_action_failure",
        lambda _client, message: sent.append(message) or "failure-message",
    )

    process_job(failed_job_id)
    # A manual retry of the same failed job must not fan out another notice.
    process_job(failed_job_id)

    with factory() as session:
        failed = session.get(Job, failed_job_id)
        notice = session.scalar(
            select(Job).where(
                Job.kind == "notify_telegram_action_failure"
            )
        )
        assert failed.status == JobStatus.failed.value
        assert "not found" in failed.last_error.lower()
        assert notice is not None
        assert session.scalar(
            select(func.count(Job.id)).where(
                Job.kind == "notify_telegram_action_failure"
            )
        ) == 1
        notice_id = notice.id
        operator_message = notice.payload["message"]
        assert "Scrape Anomaly confirm" in operator_message
        assert "record was not changed" in operator_message
        assert "not found" not in operator_message.lower()
        assert str(missing_anomaly_id) not in operator_message

    process_job(notice_id)

    with factory() as session:
        notice = session.get(Job, notice_id)
        assert notice.status == JobStatus.complete.value
        assert sent == [operator_message]


def test_failure_notification_retry_never_sends_twice_after_unknown_outcome(
    convergence_db,
    tmp_path,
    monkeypatch,
):
    factory = convergence_db
    settings = _settings(tmp_path)
    with factory.begin() as session:
        notice = Job(
            kind="notify_telegram_action_failure",
            payload={"message": "A normalized action failure."},
            status=JobStatus.running.value,
        )
        session.add(notice)
        session.flush()
        notice_id = notice.id

    sends = 0

    def accepted_then_worker_dies(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        raise KeyboardInterrupt("worker stopped after Telegram accepted")

    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_action_failure",
        accepted_then_worker_dies,
    )

    with pytest.raises(
        KeyboardInterrupt,
        match="worker stopped after Telegram accepted",
    ):
        process_job(notice_id)

    # A worker retry sees the durable "sending" marker and refuses to risk a
    # duplicate Telegram message.
    process_job(notice_id)

    with factory() as session:
        notice = session.get(Job, notice_id)
        assert sends == 1
        assert notice.status == JobStatus.failed.value
        assert notice.payload["delivery_state"] == "unknown"
        assert "not sent again" in notice.last_error
