"""Administrator acquisition review and optimization contracts (#68).

The load-bearing test in this file is
``test_the_endpoint_delegates_to_the_shared_durable_command``. A Scrape Anomaly
decision publishes or discards a staged Scraper Dataset under a dataset lock,
verifies a checksum, supersedes itself when a newer run exists, and records its
own Activity. Telegram already reaches that through
``jawnix_data.scraper.decide_scrape_anomaly``; a browser reaching it through a
second implementation would give one decision two behaviours, which is the
failure #68 exists to prevent and the thing #69 builds on.
"""

from __future__ import annotations

import sqlite3
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.models import (
    AuditEntry,
    DatasetPublication,
    Job,
    ScrapeAnomaly,
    ScrapeSegmentResult,
    ScraperConfiguration,
    ScraperRun,
    SourceRecommendation,
    SourceSegment,
)

from test_scraper import _google_maps_dataset


ADMIN_ID = uuid.uuid4()


def as_admin(session, settings: Settings) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=ADMIN_ID,
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    return TestClient(app)


def held_anomaly(session, tmp_path, monkeypatch):
    """Drive a real Scrape Run into `held_anomaly` and return its anomaly.

    Built through the production service rather than inserted, so the endpoint
    is exercised against a genuinely staged dataset with a real checksum.
    """
    from jawnix_data.scraper import run_scrape

    dataset = tmp_path / "leads.db"
    _google_maps_dataset(dataset)
    configuration = ScraperConfiguration(
        version=1,
        checksum="b" * 64,
        status="active",
        anomaly_thresholds={
            "down_fraction": 0.5,
            "up_multiplier": 2.0,
            "history_runs": 7,
        },
        created_by=uuid.uuid4(),
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
    return session.query(ScrapeAnomaly).one(), settings, dataset


class TestOneDecisionPath:
    def test_the_endpoint_delegates_to_the_shared_durable_command(
        self, session, tmp_path, monkeypatch
    ):
        """The guard against a second implementation appearing later.

        Telegram reaches `decide_scrape_anomaly` through the worker; the
        browser must reach the same callable. Patching it and asserting the
        endpoint calls it means a reimplementation cannot pass silently.
        """
        anomaly, settings, _ = held_anomaly(session, tmp_path, monkeypatch)
        calls: list[dict] = []

        # Mirrors the real signature so a drifting call site fails loudly here
        # rather than silently bypassing the shared command.
        def record_call(session_, settings_, anomaly_id, action, actor_id, reason):
            calls.append(
                {
                    "anomalyId": anomaly_id,
                    "action": action,
                    "actor_id": actor_id,
                    "reason": reason,
                }
            )
            return {"status": "confirmed", "anomalyId": str(anomaly_id)}

        monkeypatch.setattr(
            "jawnix_data.scraper.decide_scrape_anomaly", record_call
        )
        client = as_admin(session, settings)
        try:
            response = client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/confirm",
                json={"reason": "Counts reflect a deliberate source change."},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert len(calls) == 1
        assert calls[0]["anomalyId"] == anomaly.id
        assert calls[0]["action"] == "confirm"
        assert calls[0]["reason"] == "Counts reflect a deliberate source change."
        # The administrator's own identity, not a Telegram actor.
        assert calls[0]["actor_id"] == str(ADMIN_ID)

    def test_confirming_publishes_the_held_dataset(
        self, session, tmp_path, monkeypatch
    ):
        anomaly, settings, dataset = held_anomaly(session, tmp_path, monkeypatch)
        before = dataset.read_bytes()

        client = as_admin(session, settings)
        try:
            response = client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/confirm",
                json={"reason": "Counts reflect a deliberate source change."},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["status"] == "confirmed"
        session.refresh(anomaly)
        assert anomaly.status == "confirmed"
        assert anomaly.decision_by == str(ADMIN_ID)
        assert session.query(DatasetPublication).count() == 1
        assert dataset.read_bytes() != before

    def test_denying_leaves_the_published_dataset_untouched(
        self, session, tmp_path, monkeypatch
    ):
        anomaly, settings, dataset = held_anomaly(session, tmp_path, monkeypatch)
        before = dataset.read_bytes()

        client = as_admin(session, settings)
        try:
            response = client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/deny",
                json={"reason": "Source outage, not a real drop."},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        session.refresh(anomaly)
        assert anomaly.status == "denied"
        assert session.query(DatasetPublication).count() == 0
        assert dataset.read_bytes() == before

    def test_a_second_decision_is_reported_as_a_duplicate(
        self, session, tmp_path, monkeypatch
    ):
        """The command's idempotency must survive being reached over HTTP."""
        anomaly, settings, _ = held_anomaly(session, tmp_path, monkeypatch)

        client = as_admin(session, settings)
        try:
            first = client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/confirm",
                json={"reason": "Deliberate source change."},
            )
            second = client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/confirm",
                json={"reason": "Double submit."},
            )
        finally:
            app.dependency_overrides.clear()

        assert first.json()["status"] == "confirmed"
        assert second.json() == {
            "status": "confirmed",
            "duplicate": True,
            "anomalyId": str(anomaly.id),
        }
        assert session.query(DatasetPublication).count() == 1

    def test_the_decision_is_recorded_through_the_shared_activity_seam(
        self, session, tmp_path, monkeypatch
    ):
        anomaly, settings, _ = held_anomaly(session, tmp_path, monkeypatch)

        client = as_admin(session, settings)
        try:
            client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/confirm",
                json={"reason": "Deliberate source change."},
            )
        finally:
            app.dependency_overrides.clear()

        entry = session.scalar(
            select(AuditEntry).where(
                AuditEntry.target_type == "scrape_anomaly"
            )
        )
        assert entry is not None
        assert entry.actor_user_id == str(ADMIN_ID)
        assert entry.reason == "Deliberate source change."


def recommendation_scenario(session, *, version: int = 4):
    """A pending Source Recommendation bound to the active configuration."""
    configuration = ScraperConfiguration(
        version=version,
        checksum=str(version) * 64,
        status="active",
        anomaly_thresholds={},
        created_by=uuid.uuid4(),
        reason="Baseline",
        segments=[
            SourceSegment(
                key="PA::roof repair",
                niche="Roofing",
                query="roof repair",
                geography="PA",
                parameters={"status": "active", "cadence_multiplier": 1.0},
            )
        ],
    )
    session.add(configuration)
    session.flush()
    recommendation = SourceRecommendation(
        niche="Roofing",
        segment_key="PA::roof repair",
        action="reduce",
        evidence={
            "state": "PA",
            "counts": {"worked": 120, "rated": 40},
            "analysis": {"eligibility": "eligible", "peerSegmentCount": 3},
        },
        evidence_checksum="e" * 64,
        configuration_version=version,
    )
    session.add(recommendation)
    session.flush()
    session.commit()
    return recommendation


class TestRecommendationsStayHumanControlled:
    """#68: evidence is exposed before a decision, and binds that decision.

    Telegram already refuses a callback whose evidence moved on
    (jawnix/worker.py, the recommendation branch). Without the same binding
    here, "expose evidence before approve or deny" would be decorative: an
    operator could read one set of numbers and have the decision applied to
    another.
    """

    def test_the_list_exposes_the_evidence_a_decision_rests_on(self, session):
        recommendation = recommendation_scenario(session)
        client = as_admin(session, Settings())
        try:
            body = client.get("/api/admin/source-recommendations").json()
        finally:
            app.dependency_overrides.clear()

        item = next(row for row in body if row["id"] == str(recommendation.id))
        assert item["evidence"]["counts"] == {"worked": 120, "rated": 40}
        assert item["evidenceChecksum"] == "e" * 64
        assert item["configurationVersion"] == 4
        assert item["status"] == "pending"

    def test_a_decision_bound_to_the_shown_evidence_is_accepted(self, session):
        recommendation = recommendation_scenario(session)
        client = as_admin(session, Settings())
        try:
            response = client.post(
                f"/api/admin/source-recommendations/{recommendation.id}/deny",
                json={
                    "reason": "Peer set is too small to act on.",
                    "evidenceChecksum": "e" * 64,
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        session.refresh(recommendation)
        assert recommendation.status == "denied"

    def test_a_decision_against_moved_evidence_is_refused(self, session):
        recommendation = recommendation_scenario(session)
        client = as_admin(session, Settings())
        try:
            response = client.post(
                f"/api/admin/source-recommendations/{recommendation.id}/approve",
                json={
                    "reason": "Acting on numbers I read earlier.",
                    "evidenceChecksum": "0" * 64,
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 409
        assert "evidence" in response.json()["detail"].lower()
        session.refresh(recommendation)
        # Human control means the refusal changes nothing at all.
        assert recommendation.status == "pending"

    def test_approval_never_starts_acquisition_on_its_own(self, session):
        """ADR 0006: a recommendation never alters acquisition automatically."""
        recommendation = recommendation_scenario(session)
        client = as_admin(session, Settings())
        try:
            response = client.post(
                f"/api/admin/source-recommendations/{recommendation.id}/approve",
                json={
                    "reason": "Peer evidence supports reducing this segment.",
                    "evidenceChecksum": "e" * 64,
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        # A new version may be scheduled, but no Scrape Run is ever launched.
        assert session.query(Job).filter_by(kind="run_scraper").count() == 0

    def test_approval_schedules_a_new_version_rather_than_rewriting_one(
        self, session
    ):
        """Scraper Configuration versions are immutable (CONTEXT.md)."""
        recommendation = recommendation_scenario(session)
        original = session.query(ScraperConfiguration).one()
        original_checksum = original.checksum

        client = as_admin(session, Settings())
        try:
            client.post(
                f"/api/admin/source-recommendations/{recommendation.id}/approve",
                json={
                    "reason": "Peer evidence supports reducing this segment.",
                    "evidenceChecksum": "e" * 64,
                },
            )
        finally:
            app.dependency_overrides.clear()

        session.refresh(original)
        assert original.checksum == original_checksum
        assert original.version == 4
        versions = sorted(
            configuration.version
            for configuration in session.query(ScraperConfiguration)
        )
        assert versions == [4, 5]


class TestWorkspaceReadModel:
    def test_the_workspace_gathers_the_acquisition_record(
        self, session, tmp_path, monkeypatch
    ):
        anomaly, settings, _ = held_anomaly(session, tmp_path, monkeypatch)
        client = as_admin(session, settings)
        try:
            body = client.get("/api/admin/acquisition").json()
        finally:
            app.dependency_overrides.clear()

        for key in (
            "nightlyReviews",
            "scrapeAnomalies",
            "sourceRecommendations",
            "nicheMappings",
            "scraperConfigurations",
        ):
            assert key in body, key
        held = next(
            item
            for item in body["scrapeAnomalies"]
            if item["id"] == str(anomaly.id)
        )
        assert held["decidable"] is True
        # The evidence the confirm/deny decision rests on. A bare segment key
        # names what tripped without saying why, so the reasons must be here.
        assert held["anomalousSegments"] == [
            {
                "key": "roofing-austin-tx",
                "reasons": ["more_than_50_percent_down"],
            }
        ]

    def test_a_decided_anomaly_is_no_longer_decidable(
        self, session, tmp_path, monkeypatch
    ):
        anomaly, settings, _ = held_anomaly(session, tmp_path, monkeypatch)
        client = as_admin(session, settings)
        try:
            client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/deny",
                json={"reason": "Source outage, not a real drop."},
            )
            body = client.get("/api/admin/acquisition").json()
        finally:
            app.dependency_overrides.clear()

        decided = next(
            item
            for item in body["scrapeAnomalies"]
            if item["id"] == str(anomaly.id)
        )
        assert decided["status"] == "denied"
        assert decided["decidable"] is False

    def test_configuration_versions_are_listed_newest_first_and_intact(
        self, session, tmp_path, monkeypatch
    ):
        _, settings, _ = held_anomaly(session, tmp_path, monkeypatch)
        client = as_admin(session, settings)
        try:
            body = client.get("/api/admin/acquisition").json()
        finally:
            app.dependency_overrides.clear()

        versions = [item["version"] for item in body["scraperConfigurations"]]
        assert versions == sorted(versions, reverse=True)
        assert all(item["checksum"] for item in body["scraperConfigurations"])

    def test_the_read_model_never_decides_anything(
        self, session, tmp_path, monkeypatch
    ):
        """Reading the workspace must not move any record."""
        anomaly, settings, _ = held_anomaly(session, tmp_path, monkeypatch)
        publications_before = session.query(DatasetPublication).count()

        client = as_admin(session, settings)
        try:
            client.get("/api/admin/acquisition")
            client.get("/api/admin/acquisition")
        finally:
            app.dependency_overrides.clear()

        session.refresh(anomaly)
        assert anomaly.status == "pending"
        assert session.query(DatasetPublication).count() == publications_before
        assert (
            session.query(AuditEntry)
            .filter(AuditEntry.target_type == "scrape_anomaly")
            .count()
            == 0
        )


class TestDecisionGuards:
    def test_an_unknown_action_is_refused(self, session, tmp_path, monkeypatch):
        anomaly, settings, _ = held_anomaly(session, tmp_path, monkeypatch)
        client = as_admin(session, settings)
        try:
            response = client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/publish",
                json={"reason": "Not a real action."},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    def test_a_reason_is_required(self, session, tmp_path, monkeypatch):
        anomaly, settings, _ = held_anomaly(session, tmp_path, monkeypatch)
        client = as_admin(session, settings)
        try:
            missing = client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/confirm"
            )
            empty = client.post(
                f"/api/admin/scrape-anomalies/{anomaly.id}/confirm",
                json={"reason": ""},
            )
        finally:
            app.dependency_overrides.clear()

        assert missing.status_code == 422
        assert empty.status_code == 422
        session.refresh(anomaly)
        assert anomaly.status == "pending"

    def test_a_missing_anomaly_is_a_404(self, session, tmp_path, monkeypatch):
        _, settings, _ = held_anomaly(session, tmp_path, monkeypatch)
        client = as_admin(session, settings)
        try:
            response = client.post(
                f"/api/admin/scrape-anomalies/{uuid.uuid4()}/confirm",
                json={"reason": "No such anomaly."},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404
