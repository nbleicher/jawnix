from __future__ import annotations

import sqlite3
import subprocess
import json
import uuid

import pytest

from jawnix.config import Settings
from jawnix.models import (
    DatasetPublication,
    InventorySyncAttempt,
    Job,
    NightlyReview,
    ScraperConfiguration,
    ScrapeAnomaly,
    ScrapeSegmentResult,
    ScraperRun,
    SourceSegment,
)
from jawnix_data.scraper import (
    decide_scrape_anomaly,
    run_scrape,
    run_nightly_attempt,
    sync_dataset_version,
    sync_scraper,
)
from jawnix_data.migration import file_sha256
from jawnix_data.restore import validate_or_replay_restored_dataset


def _google_maps_dataset(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE leads (
                phone TEXT,
                company TEXT,
                full_name TEXT,
                niche TEXT,
                state TEXT,
                source TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO leads
            VALUES ('5125550100', 'Austin Roofing', '', 'Roofing', 'TX', 'google_maps')
            """
        )


def test_google_maps_dataset_sync_is_versioned_and_checksum_idempotent(
    session,
    tmp_path,
):
    dataset = tmp_path / "leads.db"
    _google_maps_dataset(dataset)
    settings = Settings(
        JAWNIX_SCRAPER_DB_PATH=dataset,
        JAWNIX_SCRAPER_COMMAND="",
    )

    first = sync_scraper(session, settings)
    session.commit()
    second = sync_scraper(session, settings)

    assert first["imported"] == 1
    assert second == {
        "skipped": True,
        "reason": "dataset checksum already synchronized",
        "sourceVersion": first["checksum"],
    }
    run = session.query(ScraperRun).one()
    assert run.source == "google_maps"
    assert run.source_version == first["checksum"]
    assert run.checksum == first["checksum"]
    review = session.query(NightlyReview).one()
    assert review.scraper_run_id == run.id
    assert review.summary["scraper"]["observed"] == 1
    assert review.summary["scraper"]["valid"] == 1
    assert review.summary["scraper"]["quarantined"] == 0
    assert session.query(Job).filter_by(kind="notify_nightly_review").count() == 1


def test_nppes_is_not_an_acquisition_source(session, tmp_path):
    dataset = tmp_path / "leads.db"
    _google_maps_dataset(dataset)
    settings = Settings(JAWNIX_SCRAPER_DB_PATH=dataset)

    with pytest.raises(ValueError, match="Google Maps"):
        sync_scraper(session, settings, source="nppes")

    assert not hasattr(settings, "nppes_index_url")


def test_scrape_run_stages_then_atomically_publishes_or_preserves_previous_dataset(
    session,
    tmp_path,
    monkeypatch,
):
    dataset = tmp_path / "leads.db"
    _google_maps_dataset(dataset)
    original_bytes = dataset.read_bytes()
    configuration = ScraperConfiguration(
        version=1,
        checksum="a" * 64,
        status="draft",
        anomaly_thresholds={
            "down_fraction": 0.5,
            "up_multiplier": 2.0,
            "history_runs": 7,
        },
        created_by=__import__("uuid").uuid4(),
        reason="Acceptance configuration",
        segments=[
            SourceSegment(
                key="roofing-austin-tx",
                niche="Roofing",
                query="roofing contractor",
                geography="Austin, TX",
                parameters={},
            )
        ],
    )
    session.add(configuration)
    session.commit()
    settings = Settings(
        JAWNIX_SCRAPER_DB_PATH=dataset,
        JAWNIX_SCRAPER_COMMAND="fake-google-maps",
    )

    def successful_scraper(_command, check, env):
        assert check is True
        staged = env["JAWNIX_SCRAPER_DB_PATH"]
        assert staged != str(dataset)
        with sqlite3.connect(staged) as connection:
            connection.execute("DELETE FROM leads")
            connection.execute(
                """
                INSERT INTO leads
                VALUES (
                    '5125550199',
                    'Staged Roofing',
                    '',
                    'Roofing',
                    'TX',
                    'roofing-austin-tx'
                )
                """
            )

    monkeypatch.setattr("jawnix_data.scraper.subprocess.run", successful_scraper)
    completed = run_scrape(
        session,
        settings,
        configuration.id,
        manual=True,
    )
    session.commit()

    assert completed["status"] == "published"
    publication = session.query(DatasetPublication).one()
    assert publication.version == 1
    assert publication.checksum == completed["checksum"]
    assert publication.scraper_run_id == completed["runId"]
    assert dataset.read_bytes() != original_bytes
    with sqlite3.connect(dataset) as connection:
        assert connection.execute(
            "SELECT company FROM leads"
        ).fetchone()[0] == "Staged Roofing"

    published_bytes = dataset.read_bytes()

    def failed_scraper(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "fake-google-maps")

    monkeypatch.setattr("jawnix_data.scraper.subprocess.run", failed_scraper)
    failed = run_scrape(
        session,
        settings,
        configuration.id,
        manual=True,
    )
    session.commit()
    assert failed["status"] == "failed"
    assert dataset.read_bytes() == published_bytes
    assert session.query(DatasetPublication).count() == 1
    assert [
        run.status
        for run in session.query(ScraperRun).order_by(ScraperRun.id)
    ][-2:] == ["published", "failed"]


def test_suspicious_segment_is_held_before_dataset_publication(
    session,
    tmp_path,
    monkeypatch,
):
    dataset = tmp_path / "leads.db"
    _google_maps_dataset(dataset)
    published_bytes = dataset.read_bytes()
    configuration = ScraperConfiguration(
        version=1,
        checksum="b" * 64,
        status="active",
        anomaly_thresholds={
            "down_fraction": 0.5,
            "up_multiplier": 2.0,
            "history_runs": 7,
        },
        created_by=__import__("uuid").uuid4(),
        reason="Anomaly configuration",
        segments=[
            SourceSegment(
                key="roofing-austin-tx",
                niche="Roofing",
                query="roofing contractor",
                geography="Austin, TX",
                parameters={},
            )
        ],
    )
    session.add(configuration)
    session.flush()
    for index in range(7):
        historical_run = ScraperRun(
            source="google_maps",
            source_version=f"historical-{index}",
            configuration_id=configuration.id,
            status="complete",
        )
        session.add(historical_run)
        session.flush()
        session.add(
            ScrapeSegmentResult(
                scraper_run_id=historical_run.id,
                segment_key="roofing-austin-tx",
                niche="Roofing",
                geography="Austin, TX",
                observed_count=100,
                valid_count=100,
                new_count=100,
                duplicate_count=0,
                quarantined_count=0,
                anomalous=False,
                anomaly_reasons=[],
            )
        )
    session.commit()
    settings = Settings(
        JAWNIX_SCRAPER_DB_PATH=dataset,
        JAWNIX_SCRAPER_COMMAND="fake-google-maps",
    )

    def low_result_scraper(_command, check, env):
        assert check is True
        staged = env["JAWNIX_SCRAPER_DB_PATH"]
        with sqlite3.connect(staged) as connection:
            connection.execute("DELETE FROM leads")
            connection.executemany(
                """
                INSERT INTO leads
                VALUES (?, ?, '', 'Roofing', 'TX', 'roofing-austin-tx')
                """,
                [
                    (f"512555{number:04d}", f"Roofing {number}")
                    for number in range(20)
                ],
            )

    monkeypatch.setattr("jawnix_data.scraper.subprocess.run", low_result_scraper)
    result = run_scrape(session, settings, configuration.id)
    session.commit()

    assert result["status"] == "held_anomaly"
    assert dataset.read_bytes() == published_bytes
    assert session.query(DatasetPublication).count() == 0
    anomaly = session.query(ScrapeAnomaly).one()
    assert anomaly.status == "pending"
    assert anomaly.dataset_checksum == result["checksum"]
    segment = session.query(ScrapeSegmentResult).filter_by(
        scraper_run_id=result["runId"]
    ).one()
    assert segment.valid_count == 20
    assert segment.anomalous is True
    assert segment.anomaly_reasons == ["more_than_50_percent_down"]
    assert session.query(Job).filter_by(kind="notify_scrape_anomaly").count() == 1

    confirmed = decide_scrape_anomaly(
        session,
        settings,
        anomaly.id,
        action="confirm",
        actor_id="telegram:6775236603",
        reason="Counts reflect a deliberate source change",
    )
    session.commit()
    assert confirmed["status"] == "confirmed"
    assert confirmed["datasetVersion"] == 1
    assert session.query(DatasetPublication).count() == 1
    assert dataset.read_bytes() != published_bytes
    session.refresh(anomaly)
    assert anomaly.status == "confirmed"
    assert anomaly.decision_by == "telegram:6775236603"

    duplicate = decide_scrape_anomaly(
        session,
        settings,
        anomaly.id,
        action="confirm",
        actor_id="telegram:6775236603",
        reason="Duplicate Telegram delivery",
    )
    assert duplicate == {
        "status": "confirmed",
        "duplicate": True,
        "anomalyId": str(anomaly.id),
    }
    assert session.query(DatasetPublication).count() == 1


def test_published_dataset_sync_failure_rolls_back_and_retries_same_version(
    session,
    tmp_path,
    monkeypatch,
):
    dataset = tmp_path / "leads.db"
    _google_maps_dataset(dataset)
    configuration = ScraperConfiguration(
        version=1,
        checksum="c" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=__import__("uuid").uuid4(),
        reason="Sync recovery",
        segments=[
            SourceSegment(
                key="google_maps",
                niche="Roofing",
                query="roofing",
                geography="Texas",
                parameters={},
            )
        ],
    )
    session.add(configuration)
    session.commit()
    settings = Settings(
        JAWNIX_SCRAPER_DB_PATH=dataset,
        JAWNIX_SCRAPER_COMMAND="fake-google-maps",
    )
    monkeypatch.setattr(
        "jawnix_data.scraper.subprocess.run",
        lambda *_args, **_kwargs: None,
    )

    original_import = __import__(
        "jawnix_data.scraper",
        fromlist=["import_scraper_sqlite"],
    ).import_scraper_sqlite
    failures = 0

    def fail_once(*args, **kwargs):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("temporary PostgreSQL outage")
        return original_import(*args, **kwargs)

    monkeypatch.setattr(
        "jawnix_data.scraper.import_scraper_sqlite",
        fail_once,
    )
    published = run_scrape(session, settings, configuration.id)
    session.commit()
    result = sync_dataset_version(
        session,
        settings,
        published["datasetVersion"],
    )
    session.commit()

    assert result["status"] == "sync_failed"
    publication = session.query(DatasetPublication).one()
    assert publication.version == result["datasetVersion"] == 1
    assert publication.sync_status == "failed"
    failed_attempt = session.query(InventorySyncAttempt).one()
    assert failed_attempt.status == "failed"
    assert failed_attempt.dataset_version == 1
    assert "temporary PostgreSQL outage" in failed_attempt.error

    retried = sync_dataset_version(session, settings, publication.version)
    session.commit()

    assert retried["status"] == "complete"
    assert retried["datasetVersion"] == 1
    assert session.query(DatasetPublication).count() == 1
    assert [
        attempt.status
        for attempt in session.query(InventorySyncAttempt).order_by(
            InventorySyncAttempt.attempt_number
        )
    ] == ["failed", "complete"]
    session.refresh(publication)
    assert publication.sync_status == "complete"


def test_nightly_attempt_activates_scheduled_configuration_and_writes_review(
    session,
    tmp_path,
    monkeypatch,
):
    dataset = tmp_path / "leads.db"
    _google_maps_dataset(dataset)
    configuration = ScraperConfiguration(
        version=1,
        checksum="d" * 64,
        status="scheduled",
        anomaly_thresholds={},
        created_by=__import__("uuid").uuid4(),
        reason="Next nightly configuration",
        segments=[
            SourceSegment(
                key="google_maps",
                niche="Roofing",
                query="roofing",
                geography="Texas",
                parameters={},
            )
        ],
    )
    session.add(configuration)
    session.commit()
    settings = Settings(
        JAWNIX_SCRAPER_DB_PATH=dataset,
        JAWNIX_SCRAPER_COMMAND="fake-google-maps",
    )
    monkeypatch.setattr(
        "jawnix_data.scraper.subprocess.run",
        lambda *_args, **_kwargs: None,
    )

    review = run_nightly_attempt(session, settings)
    session.commit()
    session.refresh(configuration)

    assert configuration.status == "active"
    assert configuration.activated_at is not None
    assert review.status == "attention"
    assert review.summary["configuration"]["version"] == 1
    assert review.summary["run"]["status"] == "published"
    assert review.summary["dataset"] == {
        "version": 1,
        "checksum": review.summary["dataset"]["checksum"],
        "syncStatus": "pending",
    }
    assert review.summary["segments"][0] == {
        "key": "google_maps",
        "niche": "Roofing",
        "geography": "Texas",
        "observed": 1,
        "valid": 1,
        "new": 1,
        "duplicate": 0,
        "quarantined": 0,
        "anomalous": False,
        "anomalyReasons": [],
    }
    assert session.query(Job).filter_by(
        kind="notify_nightly_review"
    ).count() == 1
    review.telegram_message_id = "123"
    session.commit()
    sync_dataset_version(
        session,
        settings,
        review.summary["dataset"]["version"],
    )
    session.commit()
    session.refresh(review)
    assert review.status == "complete"
    assert review.summary["run"]["status"] == "complete"
    assert review.summary["dataset"]["syncStatus"] == "complete"
    assert review.summary["inventory"] == {
        "total": 1,
        "byState": {"TX": 1},
    }
    assert session.query(Job).filter_by(
        kind="update_nightly_review_notification"
    ).count() == 1


def test_restore_rejects_older_validates_equal_and_replays_newer_dataset(
    session,
    tmp_path,
):
    active = tmp_path / "active.db"
    _google_maps_dataset(active)
    configuration = ScraperConfiguration(
        id=uuid.uuid4(),
        version=1,
        checksum="f" * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Restore baseline",
        segments=[
            SourceSegment(
                key="google_maps",
                niche="Roofing",
                query="roofing",
                geography="Texas",
                parameters={},
            )
        ],
    )
    run = ScraperRun(
        source="google_maps",
        source_version=file_sha256(active),
        configuration_id=configuration.id,
        dataset_version=2,
        checksum=file_sha256(active),
        status="complete",
    )
    session.add_all([configuration, run])
    session.flush()
    session.add(
        DatasetPublication(
            version=2,
            checksum=file_sha256(active),
            scraper_run_id=run.id,
            configuration_id=configuration.id,
            storage_path=str(active),
            sync_status="complete",
        )
    )
    session.commit()
    settings = Settings(JAWNIX_SCRAPER_DB_PATH=active)

    def write_metadata(path, version, checksum, config_version=1):
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "datasetVersion": version,
                    "checksum": checksum,
                    "configuration": {
                        "id": str(
                            configuration.id
                            if config_version == 1
                            else uuid.uuid4()
                        ),
                        "version": config_version,
                        "checksum": str(config_version) * 64,
                        "anomalyThresholds": {},
                        "reason": "Restored",
                        "createdBy": str(uuid.uuid4()),
                        "segments": [
                            {
                                "key": "google_maps",
                                "niche": "Roofing",
                                "query": "roofing",
                                "geography": "Texas",
                                "parameters": {},
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

    metadata = tmp_path / "metadata.json"
    write_metadata(metadata, 1, file_sha256(active))
    with pytest.raises(ValueError, match="older"):
        validate_or_replay_restored_dataset(
            session, settings, active, metadata
        )

    write_metadata(metadata, 2, file_sha256(active))
    equal = validate_or_replay_restored_dataset(
        session, settings, active, metadata
    )
    assert equal["status"] == "equal"
    assert equal["replayRequired"] is False

    newer = tmp_path / "newer.db"
    _google_maps_dataset(newer)
    with sqlite3.connect(newer) as connection:
        connection.execute(
            """
            INSERT INTO leads VALUES (
                '5125550198', 'Newer Restore', '', 'Roofing', 'TX',
                'google_maps'
            )
            """
        )
    write_metadata(metadata, 3, file_sha256(newer), config_version=2)
    replayed = validate_or_replay_restored_dataset(
        session,
        settings,
        newer,
        metadata,
        apply=True,
    )
    session.commit()
    assert replayed["status"] == "newer"
    assert replayed["syncStatus"] == "complete"
    publication = session.query(DatasetPublication).filter_by(
        version=3
    ).one()
    assert publication.checksum == file_sha256(newer)
    assert publication.sync_status == "complete"
