"""Setup (P7): occasional tools with their state and last change.

Seam: seeded configuration, mapping, Agency, and audit records in;
``operator_setup`` and ``GET /api/admin/setup`` out.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from jawnix.activity import record_activity
from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    Agent,
    AuditEntry,
    KeywordCandidatePool,
    ScraperConfiguration,
    ScraperRun,
    ScraperRuntimeConfigurationRevision,
    SourceNicheMapping,
    SourceSegment,
)
from jawnix.operator_setup import operator_setup


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
ADMIN = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def areas(result: dict) -> dict[str, dict]:
    return {area["key"]: area for area in result["areas"]}


def test_every_area_is_listed_in_order_even_on_an_empty_platform(session, settings):
    result = operator_setup(session, settings, now=NOW)

    assert [area["label"] for area in result["areas"]] == [
        "Scraper workspace",
        "Scraper settings",
        "Configuration versions",
        "Keywords",
        "Niche mappings",
        "Agencies",
        "Security",
        "Billing defaults",
    ]
    by_key = areas(result)
    assert by_key["scraperWorkspace"]["state"] == "No Scrape Run yet"
    assert by_key["configurations"]["state"] == "No version yet"
    assert by_key["nicheMappings"]["attention"] is False
    assert by_key["agencies"]["state"] == "0 active · 0 members"
    assert by_key["billing"]["state"] == (
        "No starting rate · platform billing off"
    )
    assert by_key["billing"]["changedNote"] == "Server setting"
    assert all(area["href"].startswith("/app/admin/") for area in result["areas"])


def test_each_area_reports_its_state_and_last_change(session, tmp_path):
    settings = Settings(
        JAWNIX_BATCH_DIR=tmp_path / "batches",
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
        JAWNIX_ENABLE_BILLING=True,
        JAWNIX_DB1_DEFAULT_LEAD_RATE_CENTS_PER_THOUSAND=500,
    )
    active = ScraperConfiguration(
        version=12,
        checksum="a" * 64,
        status="active",
        created_by=ADMIN,
        reason="Fixture",
        created_at=NOW - timedelta(days=10),
        activated_at=NOW - timedelta(days=8),
    )
    scheduled = ScraperConfiguration(
        version=13,
        checksum="b" * 64,
        status="scheduled",
        created_by=ADMIN,
        reason="Fixture",
        created_at=NOW - timedelta(days=1),
        scheduled_at=NOW - timedelta(days=1),
    )
    session.add_all([active, scheduled])
    session.flush()
    session.add_all(
        SourceSegment(
            configuration_id=active.id,
            key=f"TX::keyword {index}",
            niche="Roofing",
            query=f"keyword {index}",
            geography="TX",
        )
        for index in range(3)
    )
    session.add(
        ScraperRun(
            source="google_maps",
            source_version="1",
            status="committed",
            started_at=NOW - timedelta(hours=7),
            finished_at=NOW - timedelta(hours=6),
        )
    )
    session.add(
        ScraperRuntimeConfigurationRevision(
            before_checksum="x",
            after_checksum="y",
            configuration={
                "activeStates": ["FL", "TX"],
                "queue": {"target_depth": 50},
            },
            effects={},
            actor_user_id=ADMIN,
            created_at=NOW - timedelta(days=8),
        )
    )
    session.add(
        KeywordCandidatePool(
            id=1,
            candidates=[{"term": "roofer"}, {"term": "dentist"}],
            next_refill_at=NOW + timedelta(days=6),
        )
    )
    session.add_all(
        [
            SourceNicheMapping(
                segment_key="TX::roofing",
                state="TX",
                keyword="roofing",
                niche="Roofing",
                confirmed=False,
                updated_at=NOW - timedelta(days=1),
            ),
            SourceNicheMapping(
                segment_key="FL::dentist",
                state="FL",
                keyword="dentist",
                niche="Dental",
                confirmed=True,
                updated_at=NOW - timedelta(days=3),
            ),
        ]
    )
    agency = Agency(slug="gulf", name="Gulf Coast Agency")
    session.add(agency)
    session.flush()
    session.add_all(
        [
            Agent(slug="pinecrest", name="Pinecrest", agency_id=agency.id),
            Agent(slug="harbor", name="Harbor", agency_id=agency.id),
            Agent(
                slug="billed",
                name="Billed",
                billing_enabled=True,
                lead_rate_cents_per_thousand=500,
            ),
        ]
    )
    for action, at in (
        ("admin_mfa_enrollment_completed", NOW - timedelta(days=100)),
        ("admin_mfa_challenge_succeeded", NOW - timedelta(hours=1)),
    ):
        entry = record_activity(
            session,
            action=action,
            target_type="administrator_mfa",
            target_id=ADMIN,
            actor_id=ADMIN,
            reason="Fixture",
        )
        entry.created_at = at
    session.commit()

    by_key = areas(operator_setup(session, settings, now=NOW))

    assert by_key["scraperWorkspace"]["state"].endswith("committed")
    assert by_key["scraperSettings"]["state"] == "2 active states · queue depth 50"
    assert by_key["configurations"]["state"] == (
        "v12 active · 3 segments · v13 scheduled"
    )
    assert by_key["configurations"]["href"] == "/app/admin/setup/configurations"
    assert by_key["keywords"]["state"] == "2 keywords in reserve"
    assert by_key["keywords"]["upcoming"]["label"] == "next refill"
    assert by_key["nicheMappings"]["state"] == "1 awaiting review"
    assert by_key["nicheMappings"]["attention"] is True
    assert by_key["agencies"]["state"] == "1 active · 2 members"
    assert by_key["billing"]["state"] == "Starting rate $5.00 per 1,000"
    # A sign-in challenge is not a change to the authenticators.
    assert by_key["security"]["changedAt"].replace(tzinfo=timezone.utc) == (
        NOW - timedelta(days=100)
    )
    assert session.query(AuditEntry).count() == 2


def test_the_setup_endpoint_carries_billing_defaults(session, settings):
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=ADMIN, email="admin@example.com", role="admin", csrf="t"
    )
    try:
        response = TestClient(app).get("/api/admin/setup")
        assert response.status_code == 200
        body = response.json()
        assert len(body["areas"]) == 8
        assert body["billing"] == {
            "enabled": False,
            "startingRateCentsPerThousand": None,
            "billedCustomers": 0,
        }
    finally:
        app.dependency_overrides.clear()
