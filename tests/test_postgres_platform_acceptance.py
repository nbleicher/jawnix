from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

from jawnix.allocation import (
    allocate_request,
    decide_inventory_conflict,
    fulfill_round_robin,
)
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from jawnix.api import app, replace_user_account
from jawnix.customer_accounts import accept_user_account_invitation
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.delivery import deliver_request, mark_delivery_failed
from jawnix.jobs import claim_next_job
from jawnix.models import (
    Agency,
    AuditEntry,
    BatchArtifact,
    CustomerProfile,
    Customer,
    DatasetPublication,
    DistributionEvent,
    InventoryConflict,
    Job,
    Lead,
    LeadCorrectionEvent,
    LeadOutcome,
    LeadReport,
    LeadRequest,
    ListingObservation,
    NightlyReview,
    ScrapeAnomaly,
    ScrapeSegmentResult,
    ScraperConfiguration,
    ScraperRun,
    SourceRecommendation,
    SourceSegment,
    UserAccount,
    UserAccountInvitation,
    utcnow,
)
from jawnix.telegram import anomaly_callback_data
from jawnix.transitions import transition_request
from jawnix.worker import process_job
from jawnix.schemas import UserAccountReplace
from jawnix_data import scraper as scraper_module
from jawnix_data.restore import validate_or_replay_restored_dataset
from jawnix_data.scraper import (
    create_nightly_review,
    decide_scrape_anomaly,
    run_nightly_attempt,
    run_scrape,
)
from jawnix_data.scheduler import run_nightly_scraper


@pytest.mark.skipif(
    not os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL"),
    reason="requires the real PostgreSQL acceptance database",
)
def test_google_maps_to_customer_feedback_acceptance(
    tmp_path,
    monkeypatch,
):
    database_url = os.environ["JAWNIX_ACCEPTANCE_DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    run_key = uuid.uuid4().hex[:12]
    phone = f"469{int(run_key[:7], 16) % 10_000_000:07d}"
    acceptance_state = "VT"
    dataset = tmp_path / "leads.db"
    with sqlite3.connect(dataset) as source:
        source.execute(
            """
            CREATE TABLE leads (
                phone TEXT,
                company TEXT,
                full_name TEXT,
                niche TEXT,
                state TEXT,
                source TEXT,
                created_at TEXT
            )
            """
        )
        source.execute(
            "INSERT INTO leads VALUES (?, ?, '', 'Roofing', ?, ?, ?)",
            (
                phone,
                "Acceptance Roofing",
                acceptance_state,
                f"maps-{run_key}",
                "2026-07-25T02:00:00+00:00",
            ),
        )

    settings = Settings(
        JAWNIX_SCRAPER_DB_PATH=dataset,
        JAWNIX_SCRAPER_COMMAND="fake-google-maps",
        JAWNIX_BATCH_DIR=tmp_path / "batches",
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET="acceptance-secret-at-least-long-enough",
    )
    with factory.begin() as session:
        session.execute(
            update(LeadRequest)
            .where(
                LeadRequest.status.in_(
                    {
                        "pending",
                        "approved",
                        "processing",
                        "waiting_inventory",
                    }
                )
            )
            .values(
                status="canceled",
                status_message=(
                    "Superseded by a new isolated acceptance run."
                ),
            )
        )
        session.execute(
            update(Job)
            .where(Job.status.in_({"queued", "running"}))
            .values(
                status="failed",
                last_error=(
                    "Superseded by a new isolated acceptance run."
                ),
            )
        )
        session.execute(
            update(ScraperConfiguration)
            .where(
                ScraperConfiguration.status.in_(
                    {"active", "scheduled"}
                )
            )
            .values(status="superseded")
        )
        configuration = ScraperConfiguration(
            version=(
                int(
                    session.scalar(
                        select(
                            func.max(ScraperConfiguration.version)
                        )
                    )
                    or 0
                )
                + 1
            ),
            checksum=run_key.ljust(64, "0"),
            status="active",
            anomaly_thresholds={},
            created_by=uuid.uuid4(),
            reason="PostgreSQL acceptance configuration",
            segments=[
                SourceSegment(
                    key=f"maps-{run_key}",
                    niche="Roofing",
                    query="roofing",
                    geography="Vermont",
                    parameters={},
                )
            ],
        )
        session.add(configuration)
        session.flush()
        monkeypatch.setattr(
            "jawnix_data.scraper.subprocess.run",
            lambda *_args, **_kwargs: None,
        )
        publication_result = run_scrape(
            session,
            settings,
            configuration.id,
        )
        assert publication_result["status"] == "published"
        assert session.scalar(
            select(Lead).where(Lead.phone == phone)
        ) is None
        sync_job = session.scalar(
            select(Job).where(
                Job.kind == "sync_inventory",
                Job.payload["dataset_version"].as_integer()
                == publication_result["datasetVersion"],
            )
        )
        sync_job_id = sync_job.id
    monkeypatch.setattr("jawnix.worker.SessionLocal", factory)
    monkeypatch.setattr(
        "jawnix.worker.get_settings",
        lambda: settings,
    )
    with factory.begin() as session:
        claimed_sync = claim_next_job(
            session,
            "acceptance-sync-worker",
        )
        assert claimed_sync.id == sync_job_id
    production_import = scraper_module.import_scraper_sqlite
    sync_rows_written = Event()
    release_sync_commit = Event()

    def paused_inventory_import(*args, **kwargs):
        result = production_import(*args, **kwargs)
        sync_rows_written.set()
        if not release_sync_commit.wait(timeout=10):
            raise RuntimeError("Timed out waiting for visibility check.")
        return result

    monkeypatch.setattr(
        scraper_module,
        "import_scraper_sqlite",
        paused_inventory_import,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        sync_future = executor.submit(process_job, claimed_sync.id)
        assert sync_rows_written.wait(timeout=10)
        try:
            with factory() as reader:
                assert reader.scalar(
                    select(Lead).where(Lead.phone == phone)
                ) is None
        finally:
            release_sync_commit.set()
        sync_future.result(timeout=10)
    monkeypatch.setattr(
        scraper_module,
        "import_scraper_sqlite",
        production_import,
    )
    with factory.begin() as session:
        sync_job = session.get(Job, sync_job_id)
        assert sync_job.status == "complete"
        lead = session.scalar(select(Lead).where(Lead.phone == phone))
        assert lead is not None
        observation = session.get(
            ListingObservation,
            lead.current_listing_observation_id,
        )
        assert observation is not None and observation.valid

        customer = Customer(
            slug=f"acceptance-{run_key}",
            name="Acceptance Customer",
        )
        user_id = uuid.uuid4()
        profile = CustomerProfile(
            user_id=user_id,
            email=f"{run_key}@example.invalid",
            licensed_states=[acceptance_state],
            agent=customer,
            mapping_confirmed_at=utcnow(),
        )
        session.add_all([customer, profile])
        session.flush()
        session.add(
            UserAccount(
                auth_user_id=user_id,
                customer_id=customer.id,
                email=profile.email,
            )
        )

    with factory() as session:
        def database_override():
            yield session

        principal = Principal(
            user_id=user_id,
            email=profile.email,
            role="customer",
            csrf="acceptance",
        )
        app.dependency_overrides[get_db] = database_override
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[require_principal] = lambda: principal
        try:
            client = TestClient(app)
            created = client.post(
                "/api/me/requests",
                json={
                    "lead_count": 1,
                    "state_mode": "selected",
                    "states": [acceptance_state],
                },
            )
            assert created.status_code == 201
            request_id = uuid.UUID(created.json()["id"])
            transition_request(session, request_id, "approve")
            fulfill_round_robin(session, settings)
            session.commit()

            request = session.get(LeadRequest, request_id)
            assert request is not None and request.status == "generated"
            event = session.scalar(
                select(DistributionEvent).where(
                    DistributionEvent.request_id == request_id
                )
            )
            assert event is not None
            assert event.phone == phone
            assert event.source_kind == "google_maps"
            assert event.source_niche == "roofing"

            class ResendResponse:
                def __init__(self, status_code, payload=None):
                    self.status_code = status_code
                    self.payload = payload or {}

                def json(self):
                    return self.payload

            settings.resend_api_key = "acceptance-resend-key"
            monkeypatch.setattr(
                "jawnix.delivery.httpx.post",
                lambda *_args, **_kwargs: ResendResponse(503),
            )
            with pytest.raises(RuntimeError, match="503"):
                deliver_request(session, request_id, settings)
            session.rollback()
            mark_delivery_failed(
                session,
                request_id,
                "Acceptance transient Resend failure",
            )
            session.commit()
            monkeypatch.setattr(
                "jawnix.delivery.httpx.post",
                lambda *_args, **_kwargs: ResendResponse(
                    200,
                    {"id": f"resend-{run_key}"},
                ),
            )
            assert deliver_request(
                session,
                request_id,
                settings,
            ) == f"resend-{run_key}"
            session.commit()
            assert session.scalar(
                select(func.count(DistributionEvent.id)).where(
                    DistributionEvent.request_id == request_id
                )
            ) == 1

            outcome = client.post(
                f"/api/me/distributions/{event.id}/outcomes",
                json={"kind": "positive_response"},
            )
            assert outcome.status_code == 201
            assert session.scalar(
                select(LeadOutcome).where(
                    LeadOutcome.distribution_event_id == event.id
                )
            ) is not None

            appointment = client.post(
                f"/api/me/distributions/{event.id}/outcomes",
                json={
                    "kind": "appointment_booked",
                    "appointment_at": "2026-08-01T15:00:00Z",
                    "note": "Reported after the original delivery",
                },
            )
            assert appointment.status_code == 201
            report_response = client.post(
                f"/api/me/distributions/{event.id}/reports",
                json={
                    "reason": "wrong_business_or_title",
                    "details": "Acceptance report",
                },
            )
            assert report_response.status_code == 201
            report_id = uuid.UUID(report_response.json()["id"])

            admin = Principal(
                user_id=uuid.uuid4(),
                email="admin-acceptance@example.invalid",
                role="admin",
                csrf="acceptance-admin",
            )
            app.dependency_overrides[require_admin] = lambda: admin
            suppressed = client.put(
                f"/api/admin/leads/{lead.id}/suppression",
                json={"reason": "Acceptance suppression"},
            )
            assert suppressed.status_code == 200
            unsuppressed = client.request(
                "DELETE",
                f"/api/admin/leads/{lead.id}/suppression",
                json={"reason": "Acceptance suppression reversal"},
            )
            assert unsuppressed.status_code == 200
            corrected = client.put(
                f"/api/admin/leads/{lead.id}/correction",
                json={
                    "title": "Acceptance Roofing Corrected",
                    "reason": "Acceptance correction",
                },
            )
            assert corrected.status_code == 200
            correction_removed = client.request(
                "DELETE",
                f"/api/admin/leads/{lead.id}/correction",
                json={"reason": "Acceptance correction reversal"},
            )
            assert correction_removed.status_code == 200
            resolved = client.post(
                f"/api/admin/lead-reports/{report_id}/dismiss",
                json={"note": "Acceptance review completed"},
            )
            assert resolved.status_code == 200

            artifact = session.scalar(
                select(BatchArtifact).where(
                    BatchArtifact.request_id == request_id
                )
            )
            assert artifact is not None
            original_checksum = artifact.sha256
            artifact.expires_at = utcnow() - timedelta(days=1)
            Path(artifact.path).unlink()
            session.commit()
            regenerated = client.post(
                f"/api/admin/requests/{request_id}/artifact/regenerate",
                json={"reason": "Acceptance artifact recovery"},
            )
            assert regenerated.status_code == 200
            assert regenerated.json()["sha256"] == original_checksum

            assert session.get(LeadReport, report_id).status == "dismissed"
            assert session.scalar(
                select(func.count(LeadCorrectionEvent.id)).where(
                    LeadCorrectionEvent.lead_id == lead.id
                )
            ) == 2
            assert session.scalar(
                select(func.count(AuditEntry.id)).where(
                    AuditEntry.target_id == str(lead.id)
                )
            ) >= 4
        finally:
            app.dependency_overrides.clear()

    with factory.begin() as session:
        publication = session.scalar(
            select(DatasetPublication).where(
                DatasetPublication.version
                == publication_result["datasetVersion"]
            )
        )
        run = session.get(ScraperRun, publication.scraper_run_id)
        review = create_nightly_review(session, run)
        assert isinstance(review, NightlyReview)
        assert review.summary["dataset"]["syncStatus"] == "complete"
        assert (
            review.summary["inventory"]["byState"][
                acceptance_state
            ]
            >= 1
        )
        nightly_job = session.scalar(
            select(Job).where(
                Job.kind == "notify_nightly_review",
                Job.payload["review_id"].as_string()
                == str(review.id),
            )
        )
        nightly_job.status = "running"
        nightly_job_id = nightly_job.id
        nightly_review_id = review.id

        older_customer = Customer(
            slug=f"acceptance-older-{run_key}",
            name="Acceptance Older Customer",
        )
        newer_customer = Customer(
            slug=f"acceptance-newer-{run_key}",
            name="Acceptance Newer Customer",
        )
        unrelated_customer = Customer(
            slug=f"acceptance-unrelated-{run_key}",
            name="Acceptance Unrelated Customer",
        )
        older_user_id = uuid.uuid4()
        newer_user_id = uuid.uuid4()
        unrelated_user_id = uuid.uuid4()
        conflict_state = "RI"
        unrelated_state = "DE"
        session.execute(
            update(Lead)
            .where(
                Lead.state.in_(
                    {conflict_state, unrelated_state}
                )
            )
            .values(
                suppressed=True,
                suppression_reason=(
                    "Superseded by a new isolated acceptance run."
                ),
            )
        )
        older_profile = CustomerProfile(
            user_id=older_user_id,
            email=f"older-{run_key}@example.com",
            licensed_states=[conflict_state],
            customer=older_customer,
            mapping_confirmed_at=utcnow(),
        )
        newer_profile = CustomerProfile(
            user_id=newer_user_id,
            email=f"newer-{run_key}@example.com",
            licensed_states=[conflict_state],
            customer=newer_customer,
            mapping_confirmed_at=utcnow(),
        )
        unrelated_profile = CustomerProfile(
            user_id=unrelated_user_id,
            email=f"unrelated-{run_key}@example.com",
            licensed_states=[unrelated_state],
            customer=unrelated_customer,
            mapping_confirmed_at=utcnow(),
        )
        session.add_all(
            [
                older_customer,
                newer_customer,
                unrelated_customer,
                older_profile,
                newer_profile,
                unrelated_profile,
            ]
        )
        session.flush()
        older_request = LeadRequest(
            user_id=older_user_id,
            customer=older_customer,
            lead_count=2,
            state_mode="selected",
            states_snapshot=[conflict_state],
            delivery_email=older_profile.email,
            status="waiting_inventory",
            available_count=1,
            created_at=utcnow() - timedelta(minutes=1),
        )
        newer_request = LeadRequest(
            user_id=newer_user_id,
            customer=newer_customer,
            lead_count=1,
            state_mode="selected",
            states_snapshot=[conflict_state],
            delivery_email=newer_profile.email,
            status="approved",
        )
        unrelated_request = LeadRequest(
            user_id=unrelated_user_id,
            customer=unrelated_customer,
            lead_count=1,
            state_mode="selected",
            states_snapshot=[unrelated_state],
            delivery_email=unrelated_profile.email,
            status="approved",
        )
        shared_lead = Lead(
            phone=f"206{int(run_key[-7:], 16) % 10_000_000:07d}",
            title="Acceptance Shared Lead",
            state=conflict_state,
        )
        unrelated_lead = Lead(
            phone=f"302{int(run_key[-7:], 16) % 10_000_000:07d}",
            title="Acceptance Unrelated Lead",
            state=unrelated_state,
        )
        session.add_all(
            [
                older_request,
                newer_request,
                unrelated_request,
                shared_lead,
                unrelated_lead,
            ]
        )
        session.flush()
        rotation = fulfill_round_robin(session, settings)
        conflict = session.scalar(
            select(InventoryConflict).where(
                InventoryConflict.older_request_id
                == older_request.id,
                InventoryConflict.newer_request_id
                == newer_request.id,
            )
        )
        assert rotation["requestsFulfilled"] == 1
        assert unrelated_request.status == "generated"
        assert newer_request.status == "waiting_inventory"
        assert conflict is not None
        assert conflict.status == "pending"
        decision = decide_inventory_conflict(
            session,
            conflict.id,
            "deny",
            "acceptance-admin",
            "Preserve older work",
        )
        duplicate_decision = decide_inventory_conflict(
            session,
            conflict.id,
            "deny",
            "acceptance-admin",
            "Duplicate callback",
        )
        assert decision["status"] == "denied"
        assert duplicate_decision["duplicate"] is True
        unchanged_rotation = fulfill_round_robin(session, settings)
        assert unchanged_rotation["requestsFulfilled"] == 0
        assert session.scalar(
            select(func.count(InventoryConflict.id)).where(
                InventoryConflict.older_request_id
                == older_request.id,
                InventoryConflict.newer_request_id
                == newer_request.id,
            )
        ) == 1

        older_request.lead_count = 3
        changed_rotation = fulfill_round_robin(session, settings)
        assert changed_rotation["requestsFulfilled"] == 0
        reopened_conflict = session.scalar(
            select(InventoryConflict)
            .where(
                InventoryConflict.older_request_id
                == older_request.id,
                InventoryConflict.newer_request_id
                == newer_request.id,
                InventoryConflict.status == "pending",
            )
            .order_by(InventoryConflict.created_at.desc())
        )
        assert reopened_conflict is not None
        assert reopened_conflict.id != conflict.id
        confirmed = decide_inventory_conflict(
            session,
            reopened_conflict.id,
            "confirm",
            "acceptance-admin",
            "Authorize one changed-snapshot attempt",
        )
        assert confirmed["status"] == "confirmed"
        confirmed_rotation = fulfill_round_robin(session, settings)
        assert confirmed_rotation["requestsFulfilled"] == 1
        assert newer_request.status == "generated"
        session.refresh(reopened_conflict)
        assert reopened_conflict.status == "consumed"
        assert session.scalar(
            select(func.count(DistributionEvent.id)).where(
                DistributionEvent.request_id == newer_request.id
            )
        ) == 1

        configured_segment = f"maps-{run_key}"
        peer_segment = f"peer-{run_key}"
        phone_seed = int(run_key[:5], 16) % 10_000
        for segment_index, segment_key in enumerate(
            [configured_segment, peer_segment]
        ):
            for outcome_index in range(100):
                performance_lead = Lead(
                    phone=(
                        f"7{phone_seed:04d}"
                        f"{segment_index}{outcome_index:04d}"
                    ),
                    title=(
                        f"Acceptance {segment_key} "
                        f"{outcome_index}"
                    ),
                    state="TX",
                )
                session.add(performance_lead)
                session.flush()
                performance_event = DistributionEvent(
                    lead_id=performance_lead.id,
                    customer_id=customer.id,
                    customer_name=customer.name,
                    phone=performance_lead.phone,
                    title=performance_lead.title,
                    state="TX",
                    source_kind="google_maps",
                    source_segment_key=segment_key,
                    source_niche=f"Acceptance-{run_key}",
                    distribution_period="2026-06",
                    delivered_at=datetime(
                        2026,
                        6,
                        15,
                        tzinfo=timezone.utc,
                    ),
                    source=f"acceptance-{run_key}",
                )
                session.add(performance_event)
                session.flush()
                if outcome_index < 30:
                    session.add(
                        LeadOutcome(
                            distribution_event_id=performance_event.id,
                            customer_id=customer.id,
                            kind=(
                                "good"
                                if segment_key == configured_segment
                                else "poor"
                            ),
                            metric="quality",
                        )
                    )
                if (
                    segment_key == configured_segment
                    and outcome_index < 20
                ):
                    session.add(
                        LeadOutcome(
                            distribution_event_id=performance_event.id,
                            customer_id=customer.id,
                            kind="positive_response",
                            metric="positive_response",
                        )
                    )
                if (
                    segment_key == configured_segment
                    and outcome_index < 10
                ):
                    session.add(
                        LeadOutcome(
                            distribution_event_id=performance_event.id,
                            customer_id=customer.id,
                            kind="appointment_booked",
                            metric="appointment_booked",
                            appointment_at=datetime(
                                2026,
                                8,
                                1,
                                tzinfo=timezone.utc,
                            ),
                        )
                    )
        for history_index in range(7):
            historical_run = ScraperRun(
                source="google_maps",
                source_version=(
                    f"acceptance-history-{run_key}-{history_index}"
                ),
                configuration_id=configuration.id,
                status="complete",
                details={},
                finished_at=(
                    utcnow() - timedelta(days=history_index + 1)
                ),
            )
            session.add(historical_run)
            session.flush()
            session.add(
                ScrapeSegmentResult(
                    scraper_run_id=historical_run.id,
                    segment_key=f"maps-{run_key}",
                    niche="Roofing",
                    geography="Texas",
                    observed_count=10,
                    valid_count=10,
                    new_count=10,
                    duplicate_count=0,
                    quarantined_count=0,
                    anomalous=False,
                    anomaly_reasons=[],
                )
            )
        metadata_path = tmp_path / "dataset-metadata.json"
        metadata_path.write_text(
            __import__("json").dumps(
                {
                    "schemaVersion": 1,
                    "datasetVersion": publication.version,
                    "checksum": publication.checksum,
                    "configuration": {
                        "id": str(configuration.id),
                        "version": configuration.version,
                        "checksum": configuration.checksum,
                        "createdBy": str(configuration.created_by),
                        "reason": configuration.reason,
                        "anomalyThresholds": (
                            configuration.anomaly_thresholds
                        ),
                        "segments": [
                            {
                                "key": segment.key,
                                "niche": segment.niche,
                                "query": segment.query,
                                "geography": segment.geography,
                                "parameters": segment.parameters,
                            }
                            for segment in configuration.segments
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        restore_result = validate_or_replay_restored_dataset(
            session,
            settings,
            Path(publication.storage_path),
            metadata_path,
        )
        assert restore_result["status"] == "equal"
        assert restore_result["replayRequired"] is False

        metadata = json.loads(
            metadata_path.read_text(encoding="utf-8")
        )
        metadata["checksum"] = "0" * 64
        metadata_path.write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
        with pytest.raises(
            ValueError,
            match="checksum does not match",
        ):
            validate_or_replay_restored_dataset(
                session,
                settings,
                Path(publication.storage_path),
                metadata_path,
            )

        metadata["checksum"] = publication.checksum
        metadata["datasetVersion"] = publication.version - 1
        metadata_path.write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
        with pytest.raises(
            ValueError,
            match="older than PostgreSQL",
        ):
            validate_or_replay_restored_dataset(
                session,
                settings,
                Path(publication.storage_path),
                metadata_path,
            )

        newer_dataset = tmp_path / "newer-restored.db"
        newer_dataset.write_bytes(
            Path(publication.storage_path).read_bytes()
        )
        restored_phone = (
            f"8{int(run_key[:8], 16) % 1_000_000_000:09d}"
        )
        with sqlite3.connect(newer_dataset) as restored:
            restored.execute(
                "INSERT INTO leads VALUES (?, ?, '', ?, 'TX', ?, ?)",
                (
                    restored_phone,
                    "Restored Acceptance Listing",
                    "Roofing",
                    f"maps-{run_key}",
                    "2026-07-26T12:00:00+00:00",
                ),
            )
        newer_checksum = hashlib.sha256(
            newer_dataset.read_bytes()
        ).hexdigest()
        newer_version = int(
            session.scalar(
                select(func.max(DatasetPublication.version))
            )
            or 0
        ) + 1
        newer_configuration_id = uuid.uuid4()
        newer_configuration_version = int(
            session.scalar(
                select(func.max(ScraperConfiguration.version))
            )
            or 0
        ) + 1
        metadata.update(
            {
                "datasetVersion": newer_version,
                "checksum": newer_checksum,
                "configuration": {
                    "id": str(newer_configuration_id),
                    "version": newer_configuration_version,
                    "checksum": run_key.ljust(64, "3"),
                    "createdBy": str(uuid.uuid4()),
                    "reason": "Acceptance newer restore",
                    "anomalyThresholds": {},
                    "segments": [
                        {
                            "key": f"maps-{run_key}",
                            "niche": "Roofing",
                            "query": "roofing",
                            "geography": "Texas",
                            "parameters": {},
                        }
                    ],
                },
            }
        )
        metadata_path.write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
        newer_restore = validate_or_replay_restored_dataset(
            session,
            settings,
            newer_dataset,
            metadata_path,
            apply=True,
        )
        assert newer_restore["status"] == "newer"
        assert newer_restore["syncStatus"] == "complete"
        assert session.scalar(
            select(Lead).where(Lead.phone == restored_phone)
        ) is not None

    monkeypatch.setattr(
        "jawnix_data.scheduler.SessionLocal",
        factory,
    )
    scheduler_review = run_nightly_scraper(settings)
    with factory() as session:
        anomaly = session.scalar(
            select(ScrapeAnomaly)
            .where(
                ScrapeAnomaly.configuration_id == configuration.id,
                ScrapeAnomaly.status == "pending",
            )
            .order_by(ScrapeAnomaly.created_at.desc())
        )
        assert anomaly is not None
        held_run = session.get(ScraperRun, anomaly.scraper_run_id)
        held_path = Path(held_run.staged_path)
        segment_result = session.scalar(
            select(ScrapeSegmentResult).where(
                ScrapeSegmentResult.scraper_run_id == held_run.id
            )
        )
        assert segment_result.anomaly_reasons == [
            "more_than_50_percent_down"
        ]
        assert scheduler_review.scraper_run_id == held_run.id
        recommendation = session.scalar(
            select(SourceRecommendation).where(
                SourceRecommendation.segment_key
                == configured_segment,
                SourceRecommendation.action == "expand",
                SourceRecommendation.status == "pending",
            )
        )
        assert recommendation is not None
        recommendation_id = recommendation.id
        denied_recommendation = session.scalar(
            select(SourceRecommendation).where(
                SourceRecommendation.segment_key == peer_segment,
                SourceRecommendation.action.in_({"pause", "reduce"}),
                SourceRecommendation.status == "pending",
            )
        )
        assert denied_recommendation is not None
        denied_recommendation_id = denied_recommendation.id
        anomaly_id = anomaly.id
        anomaly_dataset_checksum = anomaly.dataset_checksum

    settings.telegram_webhook_secret = "acceptance-webhook-secret"
    settings.telegram_chat_id = "-10020260726"
    settings.telegram_approver_user_ids = "42"
    monkeypatch.setattr(
        "jawnix.api.TelegramClient.answer_callback",
        lambda *_args, **_kwargs: None,
    )

    def submit_anomaly_callback(
        update_id: int,
        user_id: int,
    ):
        with factory() as session:
            def callback_database_override():
                yield session

            app.dependency_overrides[get_db] = (
                callback_database_override
            )
            app.dependency_overrides[get_settings] = lambda: settings
            try:
                return TestClient(app).post(
                    "/api/integrations/telegram/webhook",
                    headers={
                        "X-Telegram-Bot-Api-Secret-Token": (
                            settings.telegram_webhook_secret
                        )
                    },
                    json={
                        "update_id": update_id,
                        "callback_query": {
                            "id": f"callback-{update_id}",
                            "from": {"id": user_id},
                            "message": {
                                "chat": {
                                    "id": int(
                                        settings.telegram_chat_id
                                    )
                                }
                            },
                            "data": anomaly_callback_data(
                                "deny",
                                anomaly_id,
                                dataset_checksum=anomaly_dataset_checksum,
                                configuration_version=configuration.version,
                            ),
                        },
                    },
                )
            finally:
                app.dependency_overrides.clear()

    telegram_update_base = int(run_key, 16)
    unauthorized = submit_anomaly_callback(telegram_update_base, 99)
    assert unauthorized.status_code == 200
    assert unauthorized.json()["ignored"] is True
    queued = submit_anomaly_callback(telegram_update_base + 1, 42)
    assert queued.status_code == 200
    assert queued.json()["queued"] is True
    duplicate = submit_anomaly_callback(telegram_update_base + 1, 42)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    with factory.begin() as session:
        decision_job = session.scalar(
            select(Job).where(
                Job.kind == "telegram_anomaly_action",
                Job.payload["anomaly_id"].as_string()
                == str(anomaly_id),
                Job.status == "queued",
            )
        )
        session.execute(
            update(Job)
            .where(
                Job.id != decision_job.id,
                Job.status.in_({"queued", "running"}),
            )
            .values(status="failed")
        )
        claimed_decision = claim_next_job(
            session,
            "acceptance-anomaly-worker",
        )
        assert claimed_decision.id == decision_job.id
    process_job(claimed_decision.id)
    with factory() as session:
        anomaly = session.get(ScrapeAnomaly, anomaly_id)
        assert anomaly.status == "denied"
        assert not held_path.exists()

    stale = submit_anomaly_callback(telegram_update_base + 2, 42)
    assert stale.status_code == 200
    assert stale.json()["queued"] is True
    with factory.begin() as session:
        stale_job = session.scalar(
            select(Job).where(
                Job.kind == "telegram_anomaly_action",
                Job.payload["anomaly_id"].as_string()
                == str(anomaly_id),
                Job.status == "queued",
            )
        )
        session.execute(
            update(Job)
            .where(
                Job.id != stale_job.id,
                Job.status.in_({"queued", "running"}),
            )
            .values(status="failed")
        )
        claimed_stale = claim_next_job(
            session,
            "acceptance-stale-anomaly-worker",
        )
        assert claimed_stale.id == stale_job.id
    process_job(claimed_stale.id)
    with factory() as session:
        assert session.scalar(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.action == "scrape_anomaly_denied",
                AuditEntry.target_id == str(anomaly_id),
            )
        ) == 1

    monkeypatch.setattr(
        "jawnix.worker.TelegramClient.post_nightly_review",
        lambda *_args, **_kwargs: f"nightly-{run_key}",
    )
    process_job(nightly_job_id)
    with factory() as session:
        nightly_review = session.get(
            NightlyReview,
            nightly_review_id,
        )
        assert nightly_review.telegram_delivery_state == "sent"
        assert (
            nightly_review.telegram_message_id
            == f"nightly-{run_key}"
        )

    with factory() as session:
        def admin_database_override():
            yield session

        app.dependency_overrides[get_db] = admin_database_override
        app.dependency_overrides[require_admin] = lambda: Principal(
            user_id=uuid.uuid4(),
            email="admin-acceptance@example.invalid",
            role="admin",
            csrf="acceptance-admin",
        )
        try:
            approved = TestClient(app).post(
                (
                    "/api/admin/source-recommendations/"
                    f"{recommendation_id}/approve"
                ),
                json={"reason": "Acceptance recommendation approval"},
            )
            assert approved.status_code == 200
            next_configuration = session.get(
                ScraperConfiguration,
                uuid.UUID(
                    approved.json()["resultingConfigurationId"]
                ),
            )
            assert next_configuration.version > configuration.version
            denied = TestClient(app).post(
                (
                    "/api/admin/source-recommendations/"
                    f"{denied_recommendation_id}/deny"
                ),
                json={"reason": "Keep the weaker source unchanged."},
            )
            assert denied.status_code == 200
            assert denied.json()["status"] == "denied"
            denied_recommendation = session.get(
                SourceRecommendation,
                denied_recommendation_id,
            )
            assert denied_recommendation.resulting_configuration_id is None
        finally:
            app.dependency_overrides.clear()

    replacement_ids = [uuid.uuid4(), uuid.uuid4()]
    concurrent_admin = Principal(
        user_id=uuid.uuid4(),
        email="concurrent-admin@example.invalid",
        role="admin",
        csrf="concurrent-admin",
    )

    with factory() as session:
        incumbent_account_id = session.scalar(
            select(UserAccount.auth_user_id).where(
                UserAccount.customer_id == customer.id,
                UserAccount.active.is_(True),
            )
        )

    def replace_account(auth_user_id):
        with factory() as session:
            try:
                return replace_user_account(
                    customer.id,
                    UserAccountReplace(
                        auth_user_id=auth_user_id,
                        email=f"{auth_user_id}@example.com",
                        reason="Concurrent PostgreSQL acceptance replacement",
                    ),
                    concurrent_admin,
                    session,
                )
            except (HTTPException, IntegrityError):
                # Two administrators racing the same replacement is exactly
                # what the pending-invitation constraint exists to settle.
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(replace_account, replacement_ids))
    assert len([outcome for outcome in outcomes if outcome]) == 1
    with factory() as session:
        active_accounts = list(
            session.scalars(
                select(UserAccount).where(
                    UserAccount.customer_id == customer.id,
                    UserAccount.active.is_(True),
                )
            )
        )
        # Concurrency never displaces access. The incumbent keeps working and
        # exactly one replacement is left waiting for acceptance.
        assert len(active_accounts) == 1
        assert active_accounts[0].auth_user_id == incumbent_account_id
        invitations = list(
            session.scalars(
                select(UserAccountInvitation).where(
                    UserAccountInvitation.customer_id == customer.id,
                    UserAccountInvitation.status == "pending",
                )
            )
        )
        assert len(invitations) == 1
        assert invitations[0].auth_user_id in replacement_ids
        assert invitations[0].replaces_auth_user_id == incumbent_account_id

        accepted = accept_user_account_invitation(
            session,
            auth_user_id=invitations[0].auth_user_id,
            email=invitations[0].email,
        )
        session.commit()
        assert accepted is not None
        promoted = list(
            session.scalars(
                select(UserAccount).where(
                    UserAccount.customer_id == customer.id,
                    UserAccount.active.is_(True),
                )
            )
        )
        assert len(promoted) == 1
        assert promoted[0].auth_user_id == invitations[0].auth_user_id

    with factory.begin() as session:
        concurrent_request = LeadRequest(
            user_id=user_id,
            customer=customer,
            lead_count=1,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email=profile.email,
            status="approved",
            status_message="Approved for concurrent allocation.",
            approved_at=utcnow(),
        )
        session.add(concurrent_request)
        session.flush()
        concurrent_request_id = concurrent_request.id

    def allocate_concurrently(_attempt):
        with factory.begin() as session:
            return allocate_request(
                session,
                concurrent_request_id,
                settings,
            ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        allocation_statuses = list(
            executor.map(allocate_concurrently, range(2))
        )
    assert allocation_statuses == ["generated", "generated"]
    with factory() as session:
        assert session.scalar(
            select(func.count(DistributionEvent.id)).where(
                DistributionEvent.request_id == concurrent_request_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(BatchArtifact.id)).where(
                BatchArtifact.request_id == concurrent_request_id
            )
        ) == 1

    fairness_state = "ND"
    with factory.begin() as session:
        session.execute(
            update(Lead)
            .where(Lead.state == fairness_state)
            .values(
                suppressed=True,
                suppression_reason=(
                    "Superseded by concurrent fairness acceptance."
                ),
            )
        )
        shared_agency = Agency(
            slug=f"acceptance-agency-{run_key}",
            name="Acceptance Shared Agency",
        )
        fairness_customers = [
            Customer(
                slug=f"acceptance-fair-{index}-{run_key}",
                name=f"Acceptance Fair Customer {index}",
                agency=shared_agency,
            )
            for index in range(2)
        ]
        fairness_profiles = [
            CustomerProfile(
                user_id=uuid.uuid4(),
                email=f"fair-{index}-{run_key}@example.com",
                licensed_states=[fairness_state],
                customer=fairness_customer,
                mapping_confirmed_at=utcnow(),
            )
            for index, fairness_customer in enumerate(
                fairness_customers
            )
        ]
        session.add_all(
            [
                shared_agency,
                *fairness_customers,
                *fairness_profiles,
            ]
        )
        session.flush()
        fairness_requests = [
            LeadRequest(
                user_id=fairness_profile.user_id,
                customer=fairness_customer,
                lead_count=1,
                state_mode="selected",
                states_snapshot=[fairness_state],
                delivery_email=fairness_profile.email,
                status="approved",
                status_message="Approved for fairness acceptance.",
                approved_at=utcnow(),
                created_at=utcnow() + timedelta(seconds=index),
            )
            for index, (
                fairness_customer,
                fairness_profile,
            ) in enumerate(
                zip(fairness_customers, fairness_profiles, strict=True)
            )
        ]
        session.add_all(
            [
                *fairness_requests,
                Lead(
                    phone=(
                        f"701{int(run_key[:7], 16) % 10_000_000:07d}"
                    ),
                    title="Acceptance Fair Lead One",
                    state=fairness_state,
                ),
                Lead(
                    phone=(
                        f"701{int(run_key[-7:], 16) % 10_000_000:07d}"
                    ),
                    title="Acceptance Fair Lead Two",
                    state=fairness_state,
                ),
            ]
        )
        session.flush()
        fairness_request_ids = [
            request.id for request in fairness_requests
        ]

    production_allocate = allocate_request
    rotation_has_locks = Event()
    release_rotation = Event()

    def paused_allocate(*args, **kwargs):
        if not rotation_has_locks.is_set():
            rotation_has_locks.set()
            if not release_rotation.wait(timeout=10):
                raise RuntimeError(
                    "Timed out waiting for the concurrent rotation."
                )
        return production_allocate(*args, **kwargs)

    monkeypatch.setattr(
        "jawnix.allocation.allocate_request",
        paused_allocate,
    )

    def run_rotation():
        with factory.begin() as session:
            return fulfill_round_robin(session, settings)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_rotation_future = executor.submit(run_rotation)
        assert rotation_has_locks.wait(timeout=10)
        try:
            second_rotation = run_rotation()
            assert second_rotation["agenciesVisited"] == 0
        finally:
            release_rotation.set()
        first_rotation_future.result(timeout=10)
    monkeypatch.setattr(
        "jawnix.allocation.allocate_request",
        production_allocate,
    )
    with factory.begin() as session:
        fairness_statuses = list(
            session.scalars(
                select(LeadRequest.status)
                .where(LeadRequest.id.in_(fairness_request_ids))
                .order_by(LeadRequest.created_at)
            )
        )
        assert fairness_statuses.count("generated") == 1
        assert fairness_statuses.count("approved") == 1
        session.execute(
            update(LeadRequest)
            .where(
                LeadRequest.id.in_(fairness_request_ids),
                LeadRequest.status == "approved",
            )
            .values(
                status="canceled",
                status_message="Fairness acceptance completed.",
            )
        )

    conflict_acceptance_state = "AK"
    with factory.begin() as session:
        session.execute(
            update(Lead)
            .where(Lead.state == conflict_acceptance_state)
            .values(
                suppressed=True,
                suppression_reason=(
                    "Superseded by concurrent conflict acceptance."
                ),
            )
        )
        concurrent_conflict_customers = [
            Customer(
                slug=f"acceptance-conflict-{index}-{run_key}",
                name=f"Acceptance Conflict Customer {index}",
            )
            for index in range(2)
        ]
        concurrent_conflict_profiles = [
            CustomerProfile(
                user_id=uuid.uuid4(),
                email=f"conflict-{index}-{run_key}@example.com",
                licensed_states=[conflict_acceptance_state],
                customer=conflict_customer,
                mapping_confirmed_at=utcnow(),
            )
            for index, conflict_customer in enumerate(
                concurrent_conflict_customers
            )
        ]
        session.add_all(
            [
                *concurrent_conflict_customers,
                *concurrent_conflict_profiles,
            ]
        )
        session.flush()
        conflict_older_request = LeadRequest(
            user_id=concurrent_conflict_profiles[0].user_id,
            customer=concurrent_conflict_customers[0],
            lead_count=2,
            state_mode="selected",
            states_snapshot=[conflict_acceptance_state],
            delivery_email=concurrent_conflict_profiles[0].email,
            status="waiting_inventory",
            status_message="Waiting for inventory.",
            available_count=1,
            created_at=utcnow() - timedelta(minutes=1),
        )
        conflict_newer_request = LeadRequest(
            user_id=concurrent_conflict_profiles[1].user_id,
            customer=concurrent_conflict_customers[1],
            lead_count=1,
            state_mode="selected",
            states_snapshot=[conflict_acceptance_state],
            delivery_email=concurrent_conflict_profiles[1].email,
            status="approved",
            status_message="Approved for conflict acceptance.",
            approved_at=utcnow(),
        )
        conflict_lead = Lead(
            phone=f"907{int(run_key[:7], 16) % 10_000_000:07d}",
            title="Acceptance Concurrent Conflict Lead",
            state=conflict_acceptance_state,
        )
        session.add_all(
            [
                conflict_older_request,
                conflict_newer_request,
                conflict_lead,
            ]
        )
        session.flush()
        fulfill_round_robin(session, settings)
        concurrent_conflict = session.scalar(
            select(InventoryConflict).where(
                InventoryConflict.older_request_id
                == conflict_older_request.id,
                InventoryConflict.newer_request_id
                == conflict_newer_request.id,
                InventoryConflict.status == "pending",
            )
        )
        assert concurrent_conflict is not None
        concurrent_conflict_id = concurrent_conflict.id
        conflict_newer_request_id = conflict_newer_request.id
        conflict_lead_id = conflict_lead.id

    def confirm_conflict(_attempt):
        with factory.begin() as session:
            return decide_inventory_conflict(
                session,
                concurrent_conflict_id,
                "confirm",
                "acceptance-concurrent-admin",
                "Authorize exactly one concurrent attempt.",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_decisions = list(
            executor.map(confirm_conflict, range(2))
        )
    assert all(
        decision["status"] == "confirmed"
        for decision in concurrent_decisions
    )
    assert sum(
        bool(decision.get("duplicate"))
        for decision in concurrent_decisions
    ) == 1

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _attempt: run_rotation(), range(2)))
    with factory() as session:
        concurrent_conflict = session.get(
            InventoryConflict,
            concurrent_conflict_id,
        )
        assert concurrent_conflict.status == "consumed"
        assert session.get(
            LeadRequest,
            conflict_newer_request_id,
        ).status == "generated"
        assert session.scalar(
            select(func.count(DistributionEvent.id)).where(
                DistributionEvent.request_id
                == conflict_newer_request_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(DistributionEvent.id)).where(
                DistributionEvent.lead_id == conflict_lead_id
            )
        ) == 1
        assert session.scalar(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.action == "inventory_conflict_confirmed",
                AuditEntry.target_id == str(concurrent_conflict_id),
            )
        ) == 1

    with factory.begin() as session:
        stale_cross_configuration = run_scrape(
            session,
            settings,
            configuration.id,
            manual=True,
        )
        assert stale_cross_configuration["status"] == "held_anomaly"
        stale_cross_anomaly = session.scalar(
            select(ScrapeAnomaly).where(
                ScrapeAnomaly.scraper_run_id
                == stale_cross_configuration["runId"]
            )
        )
        assert stale_cross_anomaly is not None
        stale_cross_anomaly_id = stale_cross_anomaly.id
        session.execute(
            update(ScraperConfiguration)
            .where(ScraperConfiguration.status == "scheduled")
            .values(status="schedule_replaced")
        )
        cross_configuration = ScraperConfiguration(
            version=(
                int(
                    session.scalar(
                        select(func.max(ScraperConfiguration.version))
                    )
                    or 0
                )
                + 1
            ),
            checksum=run_key.ljust(64, "4"),
            status="scheduled",
            anomaly_thresholds={},
            created_by=uuid.uuid4(),
            reason="Cross-configuration lock acceptance",
            scheduled_at=utcnow(),
            segments=[
                SourceSegment(
                    key=f"cross-{run_key}",
                    niche="Roofing",
                    query="roofing",
                    geography="Texas",
                    parameters={},
                )
            ],
        )
        session.add(cross_configuration)
        session.flush()
        cross_configuration_id = cross_configuration.id

    cross_run_holds_lock = Event()
    release_cross_run = Event()

    def paused_cross_configuration_scraper(
        _command,
        check,
        env,
    ):
        assert check is True
        with sqlite3.connect(
            env["JAWNIX_SCRAPER_DB_PATH"]
        ) as staged:
            staged.execute(
                "UPDATE leads SET source = ?",
                (f"cross-{run_key}",),
            )
        cross_run_holds_lock.set()
        if not release_cross_run.wait(timeout=10):
            raise RuntimeError(
                "Timed out waiting for the stale decision."
            )

    monkeypatch.setattr(
        "jawnix_data.scraper.subprocess.run",
        paused_cross_configuration_scraper,
    )

    def publish_cross_configuration():
        with factory.begin() as session:
            review = run_nightly_attempt(session, settings)
            run = session.get(ScraperRun, review.scraper_run_id)
            assert run.configuration_id == cross_configuration_id
            return {
                "status": run.status,
                "runId": run.id,
                "checksum": run.checksum,
            }

    def confirm_cross_configuration_anomaly():
        with factory.begin() as session:
            return decide_scrape_anomaly(
                session,
                settings,
                stale_cross_anomaly_id,
                "confirm",
                "acceptance-concurrent-admin",
                "Late cross-configuration callback.",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        cross_run_future = executor.submit(
            publish_cross_configuration
        )
        assert cross_run_holds_lock.wait(timeout=10)
        stale_decision_future = executor.submit(
            confirm_cross_configuration_anomaly
        )
        release_cross_run.set()
        cross_run_result = cross_run_future.result(timeout=10)
        stale_cross_result = stale_decision_future.result(timeout=10)
    assert cross_run_result["status"] == "published"
    assert stale_cross_result["status"] == "superseded"
    assert (
        stale_cross_result["newerScraperRunId"]
        == cross_run_result["runId"]
    )
    assert hashlib.sha256(
        settings.scraper_db_path.read_bytes()
    ).hexdigest() == cross_run_result["checksum"]
    with factory() as session:
        latest_publication = session.scalar(
            select(DatasetPublication)
            .order_by(DatasetPublication.version.desc())
            .limit(1)
        )
        assert latest_publication.scraper_run_id == cross_run_result["runId"]
        assert latest_publication.checksum == cross_run_result["checksum"]
    monkeypatch.setattr(
        "jawnix_data.scraper.subprocess.run",
        lambda *_args, **_kwargs: None,
    )

    def activate_configuration():
        with factory.begin() as session:
            return run_nightly_attempt(session, settings).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        nightly_review_ids = list(
            executor.map(lambda _: activate_configuration(), range(2))
        )
    assert len(set(nightly_review_ids)) == 2
    with factory() as session:
        assert session.scalar(
            select(func.count(ScraperConfiguration.id)).where(
                ScraperConfiguration.status == "active"
            )
        ) == 1
    engine.dispose()
