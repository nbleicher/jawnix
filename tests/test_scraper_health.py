"""Today's one-line Scraper health.

Seam: a signed-in administrator with or without a Scraper Privileged Session
in; ``GET /api/admin/scraper/health`` out, read through the same operations
client and grant check as the Scraper workspace.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jawnix.api import app
from jawnix.auth import CSRF_COOKIE, SESSION_COOKIE, _serializer
from jawnix.config import get_settings
from jawnix.scraper_monitoring import RegionData, health_summary
from jawnix.scraper_proxy import HEALTH_CACHE_SECONDS
from scraper_fake import ACTIVITY, REGION_PAYLOADS, WORKERS, ScraperFake
from test_scraper_workspace import (  # noqa: F401 — fixtures
    ADMIN_ID,
    PRIMARY_ID,
    enter_and_verify,
    workspace_client,
    workspace_settings,
)

CHECKED_AT = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def arm(fake: ScraperFake) -> ScraperFake:
    app.state.scraper_operations = fake
    return fake


# --- The summary ------------------------------------------------------------


def test_summary_reads_running_state_workers_queue_and_last_write():
    summary = health_summary(
        RegionData.model_validate(REGION_PAYLOADS["activity"]),
        RegionData.model_validate(REGION_PAYLOADS["workers"]),
        checked_at=CHECKED_AT,
    )

    assert summary == {
        "serviceState": "connected",
        "checkedAt": CHECKED_AT,
        "pipeline": {"key": "running", "label": "Running"},
        "workersRunning": 6,
        "workersTotal": 8,
        "queueDepth": 812,
        "latestWriteAt": datetime.fromisoformat(ACTIVITY["latest_write_at"]),
        "writeIsFresh": True,
    }


def test_summary_keeps_what_answered_when_one_region_fails():
    without_workers = health_summary(
        RegionData.model_validate(REGION_PAYLOADS["activity"]),
        None,
        checked_at=CHECKED_AT,
    )
    assert without_workers["serviceState"] == "connected"
    assert (without_workers["workersRunning"], without_workers["workersTotal"]) == (6, None)

    without_activity = health_summary(
        None,
        RegionData.model_validate(REGION_PAYLOADS["workers"]),
        checked_at=CHECKED_AT,
    )
    assert without_activity["serviceState"] == "connected"
    assert without_activity["pipeline"] is None
    assert without_activity["queueDepth"] is None
    assert without_activity["workersRunning"] is None
    assert without_activity["workersTotal"] == 8


def test_summary_counts_reporting_workers_when_no_fleet_size_is_sent():
    summary = health_summary(
        None,
        RegionData.model_validate({"workers": WORKERS}),
        checked_at=CHECKED_AT,
    )
    assert summary["workersTotal"] == len(WORKERS)


def test_summary_is_unavailable_when_nothing_answered():
    summary = health_summary(None, None, checked_at=CHECKED_AT)
    assert summary["serviceState"] == "unavailable"
    assert summary["pipeline"] is None
    assert summary["queueDepth"] is None


# --- The grant ----------------------------------------------------------------


def test_without_a_grant_the_scraper_is_never_called(workspace_client):
    client, _, _, _ = workspace_client
    fake = arm(ScraperFake())

    response = client.get("/api/admin/scraper/health")

    assert response.status_code == 200
    assert response.json() == {"granted": False}
    assert fake.monitoring_calls == []
    assert fake.operation_calls == []


def test_a_grant_from_another_sign_in_is_refused_without_calling(workspace_client):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    enter_and_verify(client, csrf)
    grant = client.cookies.get("jawnix_scraper_grant", path="/api/admin/scraper")
    fake.monitoring_calls.clear()

    # The same administrator signs in again: a new CSRF token, a new sign-in.
    token = _serializer(app.dependency_overrides[get_settings]()).dumps(
        {
            "sub": str(ADMIN_ID),
            "email": "admin@example.com",
            "role": "admin",
            "aud": "admin",
            "aal": "aal2",
            "generation": 1,
            "factor_id": str(PRIMARY_ID),
            "csrf": "a-later-sign-in",
        }
    )
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, token)
    client.cookies.set(CSRF_COOKIE, "a-later-sign-in")
    client.cookies.set("jawnix_scraper_grant", grant, path="/api/admin/scraper")

    assert client.get("/api/admin/scraper/health").json() == {"granted": False}
    assert fake.monitoring_calls == []


def test_health_needs_an_administrator(workspace_client):
    client, _, _, _ = workspace_client
    fake = arm(ScraperFake())
    client.cookies.clear()

    assert client.get("/api/admin/scraper/health").status_code == 401
    assert fake.monitoring_calls == []


# --- The read -----------------------------------------------------------------


def test_with_a_grant_it_reads_activity_and_workers(workspace_client):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    enter_and_verify(client, csrf)

    body = client.get("/api/admin/scraper/health").json()

    assert sorted(fake.monitoring_calls) == ["activity", "workers"]
    assert body["granted"] is True
    assert body["serviceState"] == "connected"
    assert body["pipeline"] == {"key": "running", "label": "Running"}
    assert (body["workersRunning"], body["workersTotal"]) == (6, 8)
    assert body["queueDepth"] == 812
    assert body["latestWriteAt"] == "2026-07-28T11:59:58+00:00"
    assert body["writeIsFresh"] is True
    assert body["checkedAt"] == "2026-07-28T12:00:00+00:00"
    assert body["lastSuccessfulAt"] == "2026-07-28T12:00:00+00:00"


def test_refreshes_inside_the_window_reuse_one_read(workspace_client):
    client, csrf, _, now = workspace_client
    fake = arm(ScraperFake())
    enter_and_verify(client, csrf)

    for _ in range(5):
        assert client.get("/api/admin/scraper/health").json()["queueDepth"] == 812
    assert len(fake.monitoring_calls) == 2

    now[0] = now[0] + timedelta(seconds=HEALTH_CACHE_SECONDS - 1)
    client.get("/api/admin/scraper/health")
    assert len(fake.monitoring_calls) == 2

    now[0] = now[0] + timedelta(seconds=1)
    body = client.get("/api/admin/scraper/health").json()
    assert len(fake.monitoring_calls) == 4
    assert body["checkedAt"] == "2026-07-28T12:00:30+00:00"


def test_the_cache_never_answers_a_sign_in_without_a_grant(workspace_client):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    enter_and_verify(client, csrf)
    assert client.get("/api/admin/scraper/health").json()["granted"] is True

    client.cookies.delete("jawnix_scraper_grant", path="/api/admin/scraper")

    assert client.get("/api/admin/scraper/health").json() == {"granted": False}
    assert len(fake.monitoring_calls) == 2


def test_a_failing_region_leaves_the_other_on_the_line(workspace_client):
    client, csrf, _, _ = workspace_client
    arm(ScraperFake(failing={"workers"}))
    enter_and_verify(client, csrf)

    body = client.get("/api/admin/scraper/health").json()

    assert body["serviceState"] == "connected"
    assert body["queueDepth"] == 812
    assert (body["workersRunning"], body["workersTotal"]) == (6, None)


def test_an_outage_is_reported_and_cached_like_a_read(workspace_client):
    client, csrf, _, now = workspace_client
    fake = arm(ScraperFake(failing={"activity", "workers"}))
    enter_and_verify(client, csrf)

    body = client.get("/api/admin/scraper/health").json()
    assert body["granted"] is True
    assert body["serviceState"] == "unavailable"
    assert body["lastSuccessfulAt"] is None
    assert body["queueDepth"] is None

    # Every refresh in the window gets the same answer without a new attempt.
    client.get("/api/admin/scraper/health")
    assert len(fake.monitoring_calls) == 2

    fake.failing.clear()
    now[0] = now[0] + timedelta(seconds=HEALTH_CACHE_SECONDS)
    assert client.get("/api/admin/scraper/health").json()["serviceState"] == "connected"
