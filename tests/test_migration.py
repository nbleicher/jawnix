from __future__ import annotations

import json
import shutil
import sqlite3
import pytest
from sqlalchemy import func, select

from jawnix.models import Agent, DistributionEvent, Lead, LeadSource, ListingObservation
from jawnix_data.migration import import_agent_config, import_distribution_history, import_manifest, import_scraper_sqlite


def test_invalid_agent_states_block_import(session, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agents": ["jo"], "agent_states": {"jo": ["TX", "IO", "CN"]}}))
    with pytest.raises(ValueError, match="CN, IO"):
        import_agent_config(session, path)
    assert session.scalar(select(func.count(Agent.id))) == 0


def test_quoted_manifest_is_idempotent_and_preserves_provenance(session, tmp_path):
    session.add(Agent(slug="jack", name="Jack"))
    session.flush()
    path = tmp_path / "manifest.csv"
    path.write_text(
        'phone,title,state,first_seen,flow,agent,date_distributed\n'
        '"(215) 555-0001","Owner, Acme",PA,2026-01-01,original,jack,2026-02-01\n'
        'bad,Bad,PA,2026-01-01,original,,\n',
        encoding="utf-8",
    )
    first = import_manifest(session, path)
    session.commit()
    second = import_manifest(session, path)

    assert first["sourceRows"] == 2 and first["imported"] == 1 and first["quarantined"] == 1
    assert second["skipped"] is True
    lead = session.scalar(select(Lead))
    assert lead.phone == "2155550001" and lead.title == "Owner, Acme"
    assert session.scalar(select(func.count(DistributionEvent.id))) == 1
    assert session.scalar(select(func.count(LeadSource.id))) == 1


def test_scraper_deduplicates_and_valid_google_maps_listing_supersedes_legacy(session, tmp_path):
    manifest_lead = Lead(phone="2145550001", title="Manifest title", state="TX", source_flow="manifest")
    session.add(manifest_lead)
    session.commit()
    path = tmp_path / "leads.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE leads (phone TEXT, company TEXT, full_name TEXT, niche TEXT, state TEXT, source TEXT)")
    connection.executemany(
        "INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("2145550001", "Scraper overwrite", "", "", "TX", "one"),
            ("(305) 555-0002", "", "Jane Doe", "", "FL", "one"),
            ("3055550002", "Other", "", "", "FL", "two"),
        ],
    )
    connection.commit()
    connection.close()

    result = import_scraper_sqlite(session, path)
    session.commit()

    assert result["sourceRows"] == 3
    assert session.scalar(select(func.count(Lead.id))) == 2
    assert session.scalar(select(Lead.title).where(Lead.phone == "2145550001")) == "Scraper overwrite"


def test_google_maps_sync_preserves_observations_and_uses_latest_valid_listing(
    session,
    tmp_path,
):
    path = tmp_path / "leads.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
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
        connection.executemany(
            "INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "4155550100",
                    "Old Valid Listing",
                    "",
                    "Roofing",
                    "CA",
                    "google_maps",
                    "2026-07-20T01:00:00+00:00",
                ),
                (
                    "4155550100",
                    "",
                    "",
                    "Roofing",
                    "NV",
                    "google_maps",
                    "2026-07-21T01:00:00+00:00",
                ),
                (
                    "4155550100",
                    "Most Recent Valid Listing",
                    "",
                    "Roofing",
                    "TX",
                    "google_maps",
                    "2026-07-22T01:00:00+00:00",
                ),
                (
                    "3055550101",
                    "No Business State",
                    "",
                    "Roofing",
                    "",
                    "google_maps",
                    "2026-07-22T01:00:00+00:00",
                ),
            ],
        )

    result = import_scraper_sqlite(session, path)
    session.commit()

    lead = session.scalar(select(Lead).where(Lead.phone == "4155550100"))
    assert lead.title == "Most Recent Valid Listing"
    assert lead.state == "TX"
    assert lead.current_listing_observation_id is not None
    assert result == {
        "sourceRows": 4,
        "imported": 1,
        "quarantined": 2,
        "observations": 4,
        "checksum": result["checksum"],
    }
    observations = list(
        session.scalars(
            select(ListingObservation).order_by(
                ListingObservation.observed_at,
                ListingObservation.row_number,
            )
        )
    )
    assert len(observations) == 4
    assert [item.valid for item in observations] == [True, False, True, False]
    assert session.scalar(
        select(Lead).where(Lead.phone == "3055550101")
    ) is None

    older_path = tmp_path / "older-leads.db"
    with sqlite3.connect(older_path) as connection:
        connection.execute(
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
        connection.execute(
            "INSERT INTO leads VALUES (?, ?, '', 'Roofing', 'CA', 'google_maps', ?)",
            (
                "4155550100",
                "Late Sync Of Older Listing",
                "2026-07-19T01:00:00+00:00",
            ),
        )
    import_scraper_sqlite(session, older_path)
    session.commit()
    session.refresh(lead)
    assert lead.title == "Most Recent Valid Listing"
    assert lead.state == "TX"


def test_scraper_checksum_is_idempotent_across_snapshot_paths(session, tmp_path):
    original = tmp_path / "leads.db"
    connection = sqlite3.connect(original)
    connection.execute("CREATE TABLE leads (phone TEXT, company TEXT, full_name TEXT, niche TEXT, state TEXT, source TEXT)")
    connection.execute("INSERT INTO leads VALUES ('3055550002', 'Acme', '', '', 'FL', 'one')")
    connection.commit()
    connection.close()
    first = import_scraper_sqlite(session, original)
    session.commit()

    copied = tmp_path / "snapshot-copy.db"
    shutil.copyfile(original, copied)
    second = import_scraper_sqlite(session, copied)

    assert first["imported"] == 1
    assert second["skipped"] is True
    assert second["checksum"] == first["checksum"]
    assert session.scalar(select(func.count(Lead.id))) == 1


def test_identifiable_csv_history_is_imported(session, tmp_path):
    agent = Agent(slug="jack", name="Jack")
    lead = Lead(phone="2155550099", title="Lead", state="PA")
    session.add_all([agent, lead])
    session.commit()
    path = tmp_path / "2026-06-01_jack_batch.csv"
    path.write_text("phone,title\n2155550099,Lead\n")

    result = import_distribution_history(session, tmp_path)
    session.commit()

    assert result["eventsCreated"] == 1
    assert session.scalar(select(func.count(DistributionEvent.id))) == 1
