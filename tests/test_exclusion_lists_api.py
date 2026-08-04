from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jawnix.allocation import inventory_count
from jawnix.api import MAX_CSV_UPLOAD_BYTES, app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    AuditEntry,
    CustomerProfile,
    DatasetPublication,
    ExclusionList,
    Job,
    Lead,
    LeadRequest,
    NightlyReview,
    RequestStatus,
    ScraperConfiguration,
    ScraperRun,
)
from jawnix.nightly import run_scheduled_nightly_review
from jawnix.telegram import TelegramClient
from jawnix.worker import process_job


def customer_client(session, settings):
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"customer-{uuid.uuid4().hex[:8]}",
        name="Protected Customer",
        licensed_states=["PA"],
    )
    profile = CustomerProfile(
        user_id=user_id,
        email=f"{user_id}@example.com",
        licensed_states=["PA"],
        agent=customer,
    )
    session.add_all([customer, profile])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    return TestClient(app), customer, user_id


def exclusion_csv(row_count: int = 1_000) -> bytes:
    phones = [f"+1 (215) 55{i // 100:01d}-{i % 10000:04d}" for i in range(row_count)]
    return ("phone\n" + "\n".join(phones) + "\n").encode()


def test_unmapped_customer_cannot_upload_or_read_admin_exclusion_lists(
    session,
    settings,
):
    user_id = uuid.uuid4()
    profile = CustomerProfile(
        user_id=user_id,
        email=f"{user_id}@example.com",
        licensed_states=[],
    )
    admin_list = ExclusionList(
        customer_id=None,
        uploaded_by="admin",
        exclusion_type="dnc",
        filename="admin.csv",
        storage_path="/tmp/admin.csv",
    )
    session.add_all([profile, admin_list])
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email=profile.email,
        role="customer",
        csrf="test",
    )
    client = TestClient(app)
    try:
        uploaded = client.post(
            "/api/me/exclusion-lists",
            data={"type": "dnc"},
            files={"file": ("customer.csv", exclusion_csv(1), "text/csv")},
        )
        assert uploaded.status_code == 404
        assert session.scalar(select(Job).where(Job.kind == "ingest_exclusion_list")) is None

        listed = client.get("/api/me/exclusion-lists")
        assert listed.status_code == 404
        status = client.get(f"/api/me/exclusion-lists/{admin_list.id}")
        assert status.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_customer_upload_is_ingested_in_the_background_and_status_is_visible(
    session,
    settings,
    monkeypatch,
):
    client, customer, user_id = customer_client(session, settings)
    try:
        response = client.post(
            "/api/me/exclusion-lists",
            data={"type": "dnc"},
            files={"file": ("customer-dnc.csv", exclusion_csv(), "text/csv")},
        )
        assert response.status_code == 202, response.text
        uploaded = response.json()
        assert uploaded["status"] == "queued"
        assert uploaded["type"] == "dnc"
        assert uploaded["filename"] == "customer-dnc.csv"

        job = session.scalar(select(Job).where(Job.kind == "ingest_exclusion_list"))
        assert job is not None
        assert job.payload == {"exclusion_list_id": uploaded["id"]}

        monkeypatch.setattr(
            "jawnix.worker.SessionLocal",
            sessionmaker(bind=session.bind, expire_on_commit=False),
        )
        monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
        process_job(job.id)
        session.expire_all()

        status = client.get(f"/api/me/exclusion-lists/{uploaded['id']}")
        assert status.status_code == 200
        assert status.json() == {
            "id": uploaded["id"],
            "type": "dnc",
            "filename": "customer-dnc.csv",
            "status": "pending_confirmation",
            "totalRows": 1_000,
            "acceptedRows": 1_000,
            "invalidRows": 0,
            "duplicateRows": 0,
            "poolImpact": 0,
            "global": False,
            "error": "",
            "createdAt": status.json()["createdAt"],
            "ingestedAt": status.json()["ingestedAt"],
            "decidedAt": None,
            "customerAvailability": None,
        }
        listed = client.get("/api/me/exclusion-lists")
        assert listed.status_code == 200
        assert listed.json() == [status.json()]
        app.dependency_overrides[require_admin] = lambda: Principal(
            user_id=uuid.uuid4(),
            email="admin@example.com",
            role="admin",
            csrf="test",
        )
        admin_view = client.get(
            f"/api/admin/customers/{customer.id}/exclusion-lists"
        )
        assert admin_view.status_code == 200
        assert admin_view.json() == [status.json()]
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "exclusion_list_uploaded"
            )
        )
        assert audit is not None
        assert audit.target_id == uploaded["id"]
        assert audit.actor_user_id == str(user_id)
        assert audit.details["customerId"] == customer.id
    finally:
        app.dependency_overrides.clear()


def test_csv_uploads_are_rejected_before_writing_unbounded_files(
    session,
    settings,
):
    client, _, _ = customer_client(session, settings)
    try:
        response = client.post(
            "/api/me/exclusion-lists",
            data={"type": "dnc"},
            files={
                "file": (
                    "too-large.csv",
                    b"x" * (MAX_CSV_UPLOAD_BYTES + 1),
                    "text/csv",
                )
            },
        )
        assert response.status_code == 413
        assert list(
            (Path(settings.batch_dir) / "exclusion-lists").glob("*.csv")
        ) == []
        assert session.scalar(select(Job).where(Job.kind == "ingest_exclusion_list")) is None
    finally:
        app.dependency_overrides.clear()


def test_rejection_preserves_uploader_protection_without_global_effect(
    session,
    settings,
    monkeypatch,
):
    client, uploader, _ = customer_client(session, settings)
    other = Agent(
        slug=f"other-{uuid.uuid4().hex[:8]}",
        name="Other Customer",
        licensed_states=["PA"],
    )
    matching = Lead(phone="2155500000", title="Match", state="PA")
    ordinary = Lead(phone="2675551212", title="Ordinary", state="PA")
    session.add_all([other, matching, ordinary])
    session.commit()
    try:
        upload = client.post(
            "/api/me/exclusion-lists",
            data={"type": "tcpa_litigator"},
            files={"file": ("litigators.csv", exclusion_csv(), "text/csv")},
        ).json()
        job = session.scalar(select(Job).where(Job.kind == "ingest_exclusion_list"))
        monkeypatch.setattr(
            "jawnix.worker.SessionLocal",
            sessionmaker(bind=session.bind, expire_on_commit=False),
        )
        monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
        process_job(job.id)
        session.expire_all()

        uploader_request = LeadRequest(
            user_id=uuid.uuid4(),
            agent_id=uploader.id,
            lead_count=1,
            states_snapshot=["PA"],
            delivery_email="uploader@example.com",
            status=RequestStatus.approved.value,
        )
        other_request = LeadRequest(
            user_id=uuid.uuid4(),
            agent_id=other.id,
            lead_count=1,
            states_snapshot=["PA"],
            delivery_email="other@example.com",
            status=RequestStatus.approved.value,
        )
        session.add_all([uploader_request, other_request])
        session.flush()
        assert inventory_count(session, uploader_request, settings) == 1
        assert inventory_count(session, other_request, settings) == 2

        app.dependency_overrides[require_admin] = lambda: Principal(
            user_id=uuid.uuid4(),
            email="admin@example.com",
            role="admin",
            csrf="test",
        )
        denied = client.post(
            f"/api/admin/exclusion-lists/{upload['id']}/deny",
            json={"reason": "Not appropriate for pool-wide use"},
        )
        assert denied.status_code == 200, denied.text
        assert denied.json()["status"] == "denied"
        session.expire_all()
        assert inventory_count(session, uploader_request, settings) == 1
        assert inventory_count(session, other_request, settings) == 2
        assert session.scalar(
            select(Lead).where(Lead.id == matching.id)
        ).suppressed is False
    finally:
        app.dependency_overrides.clear()


def test_confirmation_bars_current_and_future_leads_by_phone_without_suppression(
    session,
    settings,
    monkeypatch,
):
    client, _, _ = customer_client(session, settings)
    current_match = Lead(phone="2155500000", title="Current", state="PA")
    ordinary = Lead(phone="2675551212", title="Ordinary", state="PA")
    other = Agent(
        slug=f"other-{uuid.uuid4().hex[:8]}",
        name="Other Customer",
        licensed_states=["PA"],
    )
    session.add_all([current_match, ordinary, other])
    session.commit()
    try:
        upload = client.post(
            "/api/me/exclusion-lists",
            data={"type": "landline"},
            files={"file": ("landlines.csv", exclusion_csv(), "text/csv")},
        ).json()
        job = session.scalar(select(Job).where(Job.kind == "ingest_exclusion_list"))
        monkeypatch.setattr(
            "jawnix.worker.SessionLocal",
            sessionmaker(bind=session.bind, expire_on_commit=False),
        )
        monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
        process_job(job.id)

        app.dependency_overrides[require_admin] = lambda: Principal(
            user_id=uuid.uuid4(),
            email="admin@example.com",
            role="admin",
            csrf="test",
        )
        confirmed = client.post(
            f"/api/admin/exclusion-lists/{upload['id']}/confirm",
            json={"reason": "Confirmed customer landline evidence"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["global"] is True
        assert confirmed.json()["poolImpact"] == 1

        # This Lead did not exist when the list was uploaded, ingested, or
        # confirmed. The standing phone bar must still catch it.
        future_match = Lead(phone="2155500001", title="Future", state="PA")
        session.add(future_match)
        session.flush()
        request = LeadRequest(
            user_id=uuid.uuid4(),
            agent_id=other.id,
            lead_count=1,
            states_snapshot=["PA"],
            delivery_email="other@example.com",
            status=RequestStatus.approved.value,
        )
        session.add(request)
        session.flush()
        assert inventory_count(session, request, settings) == 1
        assert current_match.suppressed is False
        assert future_match.suppressed is False
    finally:
        app.dependency_overrides.clear()


def test_admin_bulk_upload_becomes_global_at_ingestion_and_is_audited(
    session,
    settings,
    monkeypatch,
):
    admin_id = uuid.uuid4()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=admin_id,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    client = TestClient(app)
    try:
        uploaded = client.post(
            "/api/admin/exclusion-lists",
            data={
                "type": "dnc",
                "reason": "Bulk legal DNC import",
            },
            files={"file": ("admin-dnc.csv", exclusion_csv(), "text/csv")},
        )
        assert uploaded.status_code == 202, uploaded.text
        assert uploaded.json()["status"] == "queued"

        job = session.scalar(select(Job).where(Job.kind == "ingest_exclusion_list"))
        monkeypatch.setattr(
            "jawnix.worker.SessionLocal",
            sessionmaker(bind=session.bind, expire_on_commit=False),
        )
        monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
        process_job(job.id)
        session.expire_all()

        status = client.get(
            f"/api/admin/exclusion-lists/{uploaded.json()['id']}"
        )
        assert status.status_code == 200
        assert status.json()["status"] == "confirmed"
        assert status.json()["global"] is True
        actions = [
            item.action
            for item in session.scalars(
                select(AuditEntry).order_by(AuditEntry.created_at)
            )
        ]
        assert actions == [
            "exclusion_list_uploaded",
            "exclusion_list_ingested",
        ]
        upload_audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "exclusion_list_uploaded"
            )
        )
        assert upload_audit.actor_user_id == str(admin_id)
        assert upload_audit.reason == "Bulk legal DNC import"
        assert upload_audit.details["globalOnIngestion"] is True
        ingestion_audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "exclusion_list_ingested"
            )
        )
        assert ingestion_audit.actor_user_id == str(admin_id)
        assert ingestion_audit.reason == "Bulk legal DNC import"
        assert ingestion_audit.details["poolImpact"] == 0
        assert ingestion_audit.details["global"] is True
    finally:
        app.dependency_overrides.clear()


def test_pending_list_appears_in_nightly_review_with_pool_impact_actions(
    session,
    settings,
    monkeypatch,
):
    client, _, _ = customer_client(session, settings)
    session.add(Lead(phone="2155500000", title="Current", state="PA"))
    session.commit()
    try:
        upload = client.post(
            "/api/me/exclusion-lists",
            data={"type": "dnc"},
            files={"file": ("review.csv", exclusion_csv(), "text/csv")},
        ).json()
        job = session.scalar(select(Job).where(Job.kind == "ingest_exclusion_list"))
        monkeypatch.setattr(
            "jawnix.worker.SessionLocal",
            sessionmaker(bind=session.bind, expire_on_commit=False),
        )
        monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
        process_job(job.id)
        session.expire_all()

        configuration = ScraperConfiguration(
            version=1,
            checksum="a" * 64,
            status="active",
            anomaly_thresholds={},
            created_by=uuid.uuid4(),
            reason="Test",
        )
        session.add(configuration)
        session.flush()
        run = ScraperRun(
            source="google_maps",
            source_version="exclusion-review",
            configuration_id=configuration.id,
            status="complete",
        )
        session.add(run)
        session.flush()
        session.add(
            DatasetPublication(
                version=1,
                checksum="b" * 64,
                scraper_run_id=run.id,
                configuration_id=configuration.id,
                storage_path="/tmp/test",
                sync_status="complete",
                committed_at=datetime(2026, 8, 3, 8, tzinfo=timezone.utc),
            )
        )
        session.flush()

        review = run_scheduled_nightly_review(
            session,
            settings,
            as_of=datetime(2026, 8, 3, 9, tzinfo=timezone.utc),
        )
        assert review.summary["exclusionLists"] == [
            {
                "id": upload["id"],
                "customerId": review.summary["exclusionLists"][0]["customerId"],
                "customer": "Protected Customer",
                "type": "dnc",
                "filename": "review.csv",
                "acceptedRows": 1_000,
                "poolImpact": 1,
                "status": "pending_confirmation",
            }
        ]
        item = session.get(ExclusionList, uuid.UUID(upload["id"]))
        assert item.nightly_review_id == review.id

        text, keyboard = TelegramClient(settings)._nightly_review_message(review)
        assert "Exclusion lists: 1" in text
        assert "removes 1 currently-available lead" in text
        callbacks = [
            button["callback_data"]
            for row in keyboard["inline_keyboard"]
            for button in row
        ]
        assert callbacks == [
            f"jawnix-e:confirm:{upload['id']}",
            f"jawnix-e:deny:{upload['id']}",
        ]

        # An undecided list remains pending work and must return on the next
        # Nightly Review instead of disappearing behind its first review id.
        next_run = ScraperRun(
            source="google_maps",
            source_version="exclusion-review-next",
            configuration_id=configuration.id,
            status="complete",
        )
        session.add(next_run)
        session.flush()
        session.add(
            DatasetPublication(
                version=2,
                checksum="c" * 64,
                scraper_run_id=next_run.id,
                configuration_id=configuration.id,
                storage_path="/tmp/test-next",
                sync_status="complete",
                committed_at=datetime(2026, 8, 4, 8, tzinfo=timezone.utc),
            )
        )
        session.flush()
        review.telegram_message_id = "555001"
        session.flush()
        second = run_scheduled_nightly_review(
            session,
            settings,
            as_of=datetime(2026, 8, 4, 9, tzinfo=timezone.utc),
        )
        assert second.id != review.id
        assert [row["id"] for row in second.summary["exclusionLists"]] == [
            upload["id"]
        ]
        assert session.get(ExclusionList, item.id).nightly_review_id == second.id

        # The first review's message must stop offering the decision that now
        # belongs to the second review's message.
        first = session.get(NightlyReview, review.id)
        assert [row["status"] for row in first.summary["exclusionLists"]] == [
            "carried_forward"
        ]
        text, keyboard = TelegramClient(settings)._nightly_review_message(first)
        assert "removes 1 currently-available lead" not in text
        assert keyboard["inline_keyboard"] == []
        update_jobs = list(
            session.scalars(
                select(Job).where(
                    Job.kind == "update_nightly_review_notification"
                )
            )
        )
        assert [job.payload["review_id"] for job in update_jobs] == [
            str(review.id)
        ]
    finally:
        app.dependency_overrides.clear()


def test_nightly_telegram_payload_is_bounded_for_many_pending_lists(settings):
    review = NightlyReview(
        summary={
            "exclusionLists": [
                {
                    "id": str(uuid.uuid4()),
                    "customer": "Very Long Customer Name " * 8,
                    "type": "tcpa_litigator",
                    "poolImpact": index,
                    "status": "pending_confirmation",
                }
                for index in range(100)
            ]
        }
    )

    text, keyboard = TelegramClient(settings)._nightly_review_message(review)

    assert len(text) <= 4096
    assert len(keyboard["inline_keyboard"]) <= 50
    assert "/app/admin/acquisition#nightly-reviews" in text


def test_telegram_confirmation_applies_global_exclusion_and_updates_review(
    session,
    settings,
    monkeypatch,
):
    client, _, _ = customer_client(session, settings)
    try:
        upload = client.post(
            "/api/me/exclusion-lists",
            data={"type": "dnc"},
            files={"file": ("telegram.csv", exclusion_csv(), "text/csv")},
        ).json()
        ingestion = session.scalar(
            select(Job).where(Job.kind == "ingest_exclusion_list")
        )
        worker_factory = sessionmaker(bind=session.bind, expire_on_commit=False)
        monkeypatch.setattr("jawnix.worker.SessionLocal", worker_factory)
        monkeypatch.setattr("jawnix.worker.get_settings", lambda: settings)
        process_job(ingestion.id)
        session.expire_all()

        item = session.get(ExclusionList, uuid.UUID(upload["id"]))
        review = NightlyReview(
            review_date=datetime(2026, 8, 3).date(),
            status="complete",
            summary={
                "exclusionLists": [
                    {
                        "id": upload["id"],
                        "status": "pending_confirmation",
                        "poolImpact": 0,
                    }
                ]
            },
            telegram_message_id="99",
            telegram_delivery_state="sent",
        )
        session.add(review)
        session.flush()
        item.nightly_review_id = review.id
        session.commit()

        telegram_settings = Settings(
            JAWNIX_BATCH_DIR=settings.batch_dir,
            JAWNIX_COOKIE_SECURE=False,
            JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
            TELEGRAM_WEBHOOK_SECRET="webhook-secret",
            TELEGRAM_CHAT_ID="chat-1",
            TELEGRAM_APPROVER_USER_IDS="42",
        )
        app.dependency_overrides[get_settings] = lambda: telegram_settings

        async def answered(*_args, **_kwargs):
            return None

        monkeypatch.setattr(
            "jawnix.telegram.TelegramClient.answer_callback", answered
        )
        callback = client.post(
            "/api/integrations/telegram/webhook",
            headers={
                "X-Telegram-Bot-Api-Secret-Token": "webhook-secret"
            },
            json={
                "update_id": 501,
                "callback_query": {
                    "id": "callback-501",
                    "from": {"id": 42},
                    "message": {"chat": {"id": "chat-1"}},
                    "data": f"jawnix-e:confirm:{upload['id']}",
                },
            },
        )
        assert callback.status_code == 200, callback.text
        assert callback.json() == {"ok": True, "queued": True}
        decision = session.scalar(
            select(Job).where(Job.kind == "telegram_exclusion_action")
        )
        assert decision is not None

        monkeypatch.setattr(
            "jawnix.worker.get_settings", lambda: telegram_settings
        )
        process_job(decision.id)
        session.expire_all()
        item = session.get(ExclusionList, item.id)
        assert item.status == "confirmed"
        assert item.global_effective is True
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "exclusion_list_confirmed"
            )
        )
        assert audit.actor_user_id == "telegram:42"
        refreshed_review = session.get(NightlyReview, review.id)
        assert refreshed_review.summary["exclusionLists"][0]["status"] == (
            "confirmed"
        )
        assert session.scalar(
            select(Job).where(
                Job.kind == "update_nightly_review_notification"
            )
        ) is not None
    finally:
        app.dependency_overrides.clear()
