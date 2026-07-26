from __future__ import annotations

import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

from jawnix.allocation import (
    decide_inventory_conflict,
    fulfill_round_robin,
)
from jawnix.api import app, replace_user_account
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.delivery import deliver_request, mark_delivery_failed
from jawnix.models import (
    Agent,
    AuditEntry,
    BatchArtifact,
    CustomerProfile,
    DatasetPublication,
    DistributionEvent,
    InventoryConflict,
    Lead,
    LeadCorrectionEvent,
    LeadOutcome,
    LeadReport,
    LeadRequest,
    ListingObservation,
    NightlyReview,
    ScrapeAnomaly,
    ScraperConfiguration,
    ScraperRun,
    SourceRecommendation,
    SourceSegment,
    UserAccount,
    utcnow,
)
from jawnix.transitions import transition_request
from jawnix.schemas import UserAccountReplace
from jawnix_data.restore import validate_or_replay_restored_dataset
from jawnix_data.scraper import (
    create_nightly_review,
    decide_scrape_anomaly,
    run_nightly_attempt,
    run_scrape,
    sync_dataset_version,
)


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
            "INSERT INTO leads VALUES (?, ?, '', 'Roofing', 'TX', ?, ?)",
            (
                phone,
                "Acceptance Roofing",
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
                    geography="Texas",
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
    with factory.begin() as session:
        sync_result = sync_dataset_version(
            session,
            settings,
            publication_result["datasetVersion"],
        )
        assert sync_result["imported"] == 1
        lead = session.scalar(select(Lead).where(Lead.phone == phone))
        assert lead is not None
        observation = session.get(
            ListingObservation,
            lead.current_listing_observation_id,
        )
        assert observation is not None and observation.valid

        customer = Agent(
            slug=f"acceptance-{run_key}",
            name="Acceptance Customer",
        )
        user_id = uuid.uuid4()
        profile = CustomerProfile(
            user_id=user_id,
            email=f"{run_key}@example.invalid",
            licensed_states=["TX"],
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
                    "states": ["TX"],
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
                f"/api/admin/lead-reports/{report_id}/resolve",
                json={
                    "action": "dismissed",
                    "note": "Acceptance review completed",
                },
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
        assert review.summary["inventory"]["byState"]["TX"] >= 1

        older_request = session.get(LeadRequest, request_id)
        newer_request = LeadRequest(
            user_id=user_id,
            customer_id=customer.id,
            lead_count=1,
            state_mode="selected",
            states_snapshot=["TX"],
            delivery_email=profile.email,
            status="waiting_inventory",
        )
        session.add(newer_request)
        session.flush()
        conflict = InventoryConflict(
            older_request_id=older_request.id,
            newer_request_id=newer_request.id,
            inventory_snapshot={"leadIds": [lead.id]},
            snapshot_checksum=run_key.ljust(64, "1"),
        )
        session.add(conflict)
        session.flush()
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

        recommendation = SourceRecommendation(
            niche="Roofing",
            segment_key=f"maps-{run_key}",
            action="expand",
            evidence={
                "distributionPeriod": "2026-07",
                "qualityStatus": "insufficient_data",
            },
            evidence_checksum=run_key.ljust(64, "2"),
        )
        session.add(recommendation)
        session.flush()
        recommendation_id = recommendation.id

        staging_directory = dataset.parent / ".staging"
        staging_directory.mkdir(exist_ok=True)
        held_path = staging_directory / f"held-{run_key}.db"
        held_path.write_bytes(dataset.read_bytes())
        held_checksum = __import__("hashlib").sha256(
            held_path.read_bytes()
        ).hexdigest()
        held_run = ScraperRun(
            source="google_maps",
            source_version=held_checksum,
            configuration_id=configuration.id,
            checksum=held_checksum,
            status="held_anomaly",
            staged_path=str(held_path),
            details={},
        )
        session.add(held_run)
        session.flush()
        anomaly = ScrapeAnomaly(
            scraper_run_id=held_run.id,
            configuration_id=configuration.id,
            dataset_checksum=held_checksum,
        )
        session.add(anomaly)
        session.flush()
        anomaly_result = decide_scrape_anomaly(
            session,
            settings,
            anomaly.id,
            "deny",
            "acceptance-admin",
            "Acceptance anomaly denial",
        )
        assert anomaly_result["status"] == "denied"
        assert not held_path.exists()

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
        finally:
            app.dependency_overrides.clear()

    replacement_ids = [uuid.uuid4(), uuid.uuid4()]
    concurrent_admin = Principal(
        user_id=uuid.uuid4(),
        email="concurrent-admin@example.invalid",
        role="admin",
        csrf="concurrent-admin",
    )

    def replace_account(auth_user_id):
        with factory() as session:
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

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(replace_account, replacement_ids))
    with factory() as session:
        active_accounts = list(
            session.scalars(
                select(UserAccount).where(
                    UserAccount.customer_id == customer.id,
                    UserAccount.active.is_(True),
                )
            )
        )
        assert len(active_accounts) == 1
        assert active_accounts[0].auth_user_id in replacement_ids

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
