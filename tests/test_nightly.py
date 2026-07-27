from __future__ import annotations

import uuid
from datetime import datetime, timezone

from jawnix.config import Settings
from jawnix.models import (
    DatasetPublication,
    Job,
    NightlyReview,
    ScraperConfiguration,
    ScraperRun,
)
from jawnix.nightly import run_scheduled_nightly_review
from jawnix.nightly import activate_scheduled_scraper_configuration
from jawnix.models import SourceSegment


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


def test_review_accepts_same_day_external_scraper_publication(
    session, monkeypatch
):
    class Publication:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "committed",
                "publicationDate": "2026-07-27",
                "committedAt": "2026-07-27T08:00:00+00:00",
                "businessCount": 500,
                "leadCount": 400,
                "checksum": "d" * 32,
            }

    monkeypatch.setattr(
        "jawnix.nightly.httpx.get",
        lambda *_args, **_kwargs: Publication(),
    )
    review = run_scheduled_nightly_review(
        session,
        Settings(
            JAWNIX_SCRAPER_OPS_URL="http://10.77.0.2:8090",
            JAWNIX_SCRAPER_OPS_PASSWORD="secret",
        ),
        as_of=datetime(2026, 7, 27, 9, tzinfo=timezone.utc),
    )
    assert review.status == "complete"
    assert review.summary["publication"]["source"] == (
        "scraper_operations"
    )


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
