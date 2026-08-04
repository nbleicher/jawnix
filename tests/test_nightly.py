from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from jawnix.config import Settings
from jawnix.models import (
    AuditEntry,
    DailySourcePerformance,
    DatasetPublication,
    Job,
    NightlyReview,
    PerformanceSuggestionNote,
    ScraperConfiguration,
    ScraperRun,
    ScrapeSegmentResult,
    SourceNicheMapping,
)
from jawnix.nightly import run_scheduled_nightly_review
from jawnix.nightly import activate_scheduled_scraper_configuration
from jawnix.nightly import propose_niche_mappings
from jawnix.models import SourceSegment
from jawnix_data.scraper import create_nightly_review
from scraper_fake import GenerationFake


def test_review_waits_idempotently_for_publication(session):
    settings = Settings(
        JAWNIX_NIGHTLY_REVIEW_RETRY_MINUTES=15,
    )
    first = run_scheduled_nightly_review(
        session,
        settings,
        as_of=datetime(2026, 7, 27, 9, tzinfo=timezone.utc),
    )
    second = run_scheduled_nightly_review(
        session,
        settings,
        as_of=datetime(
            2026, 7, 27, 9, 15, tzinfo=timezone.utc
        ),
    )
    assert first.id == second.id
    assert second.status == "waiting_publication"
    assert second.attempt_count == 2
    assert session.query(NightlyReview).count() == 1
    assert session.query(Job).count() == 0


def test_review_completes_once_after_committed_publication(session):
    settings = Settings()
    configuration = ScraperConfiguration(
        version=1,
        checksum="a" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Test",
    )
    run = ScraperRun(
        source="google_maps",
        source_version="nightly-test",
        configuration_id=configuration.id,
        status="complete",
    )
    session.add_all([configuration, run])
    session.flush()
    session.add(
        DatasetPublication(
            version=1,
            checksum="b" * 64,
            scraper_run_id=run.id,
            configuration_id=configuration.id,
            storage_path="/tmp/test",
            sync_status="complete",
            committed_at=datetime(
                2026, 7, 27, 8, tzinfo=timezone.utc
            ),
        )
    )
    session.flush()
    review = run_scheduled_nightly_review(
        session,
        settings,
        as_of=datetime(2026, 7, 27, 9, tzinfo=timezone.utc),
    )
    replay = run_scheduled_nightly_review(
        session,
        settings,
        as_of=datetime(
            2026, 7, 27, 9, 5, tzinfo=timezone.utc
        ),
    )
    assert review.id == replay.id
    assert review.status == "complete"
    assert review.summary["publication"]["version"] == 1
    assert review.summary["performance"]["snapshotCount"] == 0
    assert session.query(Job).filter_by(
        kind="notify_nightly_review"
    ).count() == 1


def test_scheduled_review_uses_full_segment_counts_when_run_exists(session):
    settings = Settings()
    configuration = ScraperConfiguration(
        version=1,
        checksum="a" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Test",
    )
    run = ScraperRun(
        source="google_maps",
        source_version="nightly-test",
        configuration_id=configuration.id,
        status="complete",
    )
    session.add_all([configuration, run])
    session.flush()
    session.add_all(
        [
            ScrapeSegmentResult(
                scraper_run_id=run.id,
                segment_key="OH::roof repair",
                niche="Roofing",
                geography="OH",
                observed_count=10,
                valid_count=8,
                new_count=5,
                duplicate_count=3,
                quarantined_count=2,
                anomalous=True,
                anomaly_reasons=["more_than_50_percent_down"],
            ),
            ScrapeSegmentResult(
                scraper_run_id=run.id,
                segment_key="TX::plumber",
                niche="Plumbing",
                geography="TX",
                observed_count=4,
                valid_count=4,
                new_count=4,
                duplicate_count=0,
                quarantined_count=0,
                anomalous=False,
                anomaly_reasons=[],
            ),
        ]
    )
    session.add(
        DatasetPublication(
            version=1,
            checksum="b" * 64,
            scraper_run_id=run.id,
            configuration_id=configuration.id,
            storage_path="/tmp/test",
            sync_status="complete",
            committed_at=datetime(
                2026, 7, 27, 8, tzinfo=timezone.utc
            ),
        )
    )
    session.flush()

    review = run_scheduled_nightly_review(
        session,
        settings,
        as_of=datetime(2026, 7, 27, 9, tzinfo=timezone.utc),
    )

    segments_by_key = {
        item["key"]: item for item in review.summary["segments"]
    }
    assert segments_by_key["OH::roof repair"] == {
        "key": "OH::roof repair",
        "niche": "Roofing",
        "geography": "OH",
        "observed": 10,
        "valid": 8,
        "new": 5,
        "duplicate": 3,
        "quarantined": 2,
        "anomalous": True,
        "anomalyReasons": ["more_than_50_percent_down"],
        "state": "OH",
        "keyword": "roof repair",
        "confidence": None,
        "action": None,
    }
    assert segments_by_key["TX::plumber"]["anomalous"] is False
    assert review.summary["inventory"] == {"total": 0, "byState": {}}
    assert "eligible" not in review.summary["inventory"]


def test_scheduled_review_adopts_existing_run_scoped_review(session):
    settings = Settings()
    configuration = ScraperConfiguration(
        version=1,
        checksum="c" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Test",
    )
    run = ScraperRun(
        source="google_maps",
        source_version="nightly-test-2",
        configuration_id=configuration.id,
        status="complete",
    )
    session.add_all([configuration, run])
    session.flush()
    session.add(
        ScrapeSegmentResult(
            scraper_run_id=run.id,
            segment_key="OH::roof repair",
            niche="Roofing",
            geography="OH",
            observed_count=6,
            valid_count=5,
            new_count=5,
            duplicate_count=0,
            quarantined_count=1,
            anomalous=False,
            anomaly_reasons=[],
        )
    )
    session.add(
        DatasetPublication(
            version=1,
            checksum="d" * 64,
            scraper_run_id=run.id,
            configuration_id=configuration.id,
            storage_path="/tmp/test-2",
            sync_status="complete",
            committed_at=datetime(
                2026, 7, 27, 8, tzinfo=timezone.utc
            ),
        )
    )
    session.flush()

    # The scrape-run-scoped writer claims this run's NightlyReview first,
    # exactly as it does at the end of a real nightly scrape.
    run_scoped_review = create_nightly_review(session, run)
    session.flush()

    # The calendar-day-scoped writer must not crash when it later tries to
    # attach the same scraper_run_id to a review_date-keyed row.
    scheduled_review = run_scheduled_nightly_review(
        session,
        settings,
        as_of=datetime(2026, 7, 27, 9, tzinfo=timezone.utc),
    )

    assert session.query(NightlyReview).count() == 1
    assert scheduled_review.id == run_scoped_review.id
    assert scheduled_review.scraper_run_id == run.id
    assert scheduled_review.review_date == datetime(2026, 7, 27).date()
    assert scheduled_review.status == "complete"
    segment = next(
        item
        for item in scheduled_review.summary["segments"]
        if item["key"] == "OH::roof repair"
    )
    assert segment["valid"] == 5
    assert segment["quarantined"] == 1
    assert scheduled_review.summary["performance"] == {
        "snapshotCount": 0,
        "actionableCount": 0,
    }
    assert "recommendations" in scheduled_review.summary
    assert "exclusionLists" in scheduled_review.summary
    assert "scraperOperations" in scheduled_review.summary
    assert "publication" in scheduled_review.summary


def test_review_accepts_same_day_external_scraper_publication(
    session, monkeypatch
):
    canonical_segments = [
        {
            "id": "OH::roof repair",
            "keyword": "roof repair",
            "state": "OH",
            "niche": "",
            "niche_confirmed": False,
            "status": "active",
            "cadence_multiplier": 1.0,
            "version": 1,
            "seed_segment_id": None,
        }
    ]
    contract_checksum = hashlib.sha256(
        json.dumps(
            {"version": 1, "segments": canonical_segments},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    class Response:
        status_code = 200

        def __init__(self, payload=None):
            self.payload = payload or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def get(url, **_kwargs):
        if url.endswith("/api/source-segments/publication"):
            return Response({
                "status": "committed",
                "publicationDate": "2026-07-27",
                "committedAt": "2026-07-27T08:00:00+00:00",
                "businessCount": 500,
                "leadCount": 400,
                "checksum": "d" * 32,
            })
        if url.endswith("/api/source-segments"):
            return Response({
                "version": 1,
                "checksum": contract_checksum,
                "segments": [
                    {
                        "id": "OH::roof repair",
                        "keyword": "roof repair",
                        "state": "OH",
                        "niche": "",
                        "nicheConfirmed": False,
                        "status": "active",
                        "cadenceMultiplier": 1.0,
                        "seedSegmentId": None,
                    }
                ],
            })
        return Response()

    monkeypatch.setattr(
        "jawnix.nightly.httpx.get",
        get,
    )
    monkeypatch.setattr(
        "jawnix.nightly.build_generation_provider",
        lambda _settings: GenerationFake(),
    )
    existing_review = NightlyReview(
        review_date=datetime(2026, 7, 27).date(),
        scheduled_for=datetime(
            2026, 7, 27, 9, tzinfo=timezone.utc
        ),
        status="complete",
        summary={
            "performance": {
                "snapshotCount": 0,
                "actionableCount": 0,
            }
        },
        telegram_message_id="existing-message",
        telegram_delivery_state="sent",
    )
    session.add(existing_review)
    session.flush()
    review = run_scheduled_nightly_review(
        session,
        Settings(
            JAWNIX_SCRAPER_OPS_URL="http://10.77.0.2:8090",
            JAWNIX_SCRAPER_OPS_PASSWORD="secret",
            OPENROUTER_API_KEY="test-key",
        ),
        as_of=datetime(2026, 7, 27, 9, tzinfo=timezone.utc),
    )
    assert review.status == "complete"
    assert review.id == existing_review.id
    assert review.summary["publication"]["source"] == (
        "scraper_operations"
    )
    configuration = session.query(ScraperConfiguration).one()
    assert configuration.version == 1
    assert configuration.status == "active"
    assert configuration.checksum == contract_checksum
    assert [item.key for item in configuration.segments] == [
        "OH::roof repair"
    ]
    mapping = session.query(SourceNicheMapping).one()
    assert mapping.niche == "Roofing"
    assert mapping.confirmed is False
    assert session.query(DailySourcePerformance).count() == 1
    assert session.query(PerformanceSuggestionNote).count() == 1
    assert session.query(AuditEntry).filter_by(
        action="scraper_configuration_baseline_imported"
    ).count() == 1
    assert review.summary["performance"] == {
        "snapshotCount": 1,
        "actionableCount": 0,
    }


def test_scheduled_configuration_activates_exact_scale_contract(
    session, monkeypatch
):
    configuration = ScraperConfiguration(
        version=2,
        checksum="c" * 64,
        status="scheduled",
        scheduled_at=datetime(
            2026, 7, 27, 8, tzinfo=timezone.utc
        ),
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Approved recommendation",
        segments=[
            SourceSegment(
                key="PA::roof repair",
                niche="Roofing",
                query="roof repair",
                geography="PA",
                parameters={
                    "status": "reduced",
                    "cadence_multiplier": 0.5,
                    "niche_confirmed": True,
                },
            )
        ],
    )
    session.add(configuration)
    session.flush()
    captured = {}

    class Accepted:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "version": captured["json"]["version"],
                "checksum": captured["json"]["checksum"],
            }

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Accepted()

    monkeypatch.setattr("jawnix.nightly.httpx.post", post)
    activated = activate_scheduled_scraper_configuration(
        session,
        Settings(
            JAWNIX_SCRAPER_OPS_URL="http://10.77.0.2:8090",
            JAWNIX_SCRAPER_OPS_PASSWORD="secret",
        ),
        as_of=datetime(2026, 7, 27, 8, tzinfo=timezone.utc),
    )
    assert activated.status == "active"
    assert captured["url"].endswith(
        "/api/source-segments/activate"
    )
    assert captured["json"]["segments"] == [
        {
            "id": "PA::roof repair",
            "keyword": "roof repair",
            "state": "PA",
            "niche": "Roofing",
            "niche_confirmed": True,
            "status": "reduced",
            "cadence_multiplier": 0.5,
        }
    ]
    audit = session.scalar(
        select(AuditEntry).where(
            AuditEntry.action
            == "scraper_configuration_activated"
        )
    )
    assert audit is not None
    assert audit.actor_user_id == "system:nightly-scheduler"
    assert audit.details["before"] == {"status": "scheduled"}
    assert audit.details["after"] == {"status": "active"}


def test_niche_proposal_repairs_empty_unconfirmed_mapping(
    session, monkeypatch
):
    configuration = ScraperConfiguration(
        version=1,
        checksum="c" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Imported baseline",
        segments=[
            SourceSegment(
                key="OH::roof repair",
                niche="",
                query="roof repair",
                geography="OH",
                parameters={
                    "status": "active",
                    "cadence_multiplier": 1.0,
                    "niche_confirmed": False,
                },
            )
        ],
    )
    mapping = SourceNicheMapping(
        segment_key="OH::roof repair",
        state="OH",
        keyword="roof repair",
        niche="",
        confirmed=False,
        proposal_source="scraper_configuration",
        proposed_evidence={},
    )
    session.add_all([configuration, mapping])
    session.flush()

    monkeypatch.setattr(
        "jawnix.nightly.build_generation_provider",
        lambda _settings: GenerationFake(),
    )
    updated = propose_niche_mappings(
        session,
        Settings(
            JAWNIX_SCRAPER_OPS_URL="http://10.77.0.2:8090",
            JAWNIX_SCRAPER_OPS_PASSWORD="secret",
            OPENROUTER_API_KEY="test-key",
        ),
    )
    assert updated == 1
    assert session.query(SourceNicheMapping).count() == 1
    assert mapping.niche == "Roofing"
    assert mapping.confirmed is False
    assert mapping.proposal_source == "ai_openrouter"
    assert mapping.proposed_evidence["proposalRefreshed"] is True
