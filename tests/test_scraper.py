from __future__ import annotations

import sqlite3

import pytest

from jawnix.config import Settings
from jawnix.models import Job, NightlyReview, ScraperRun
from jawnix_data.scraper import sync_scraper


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
