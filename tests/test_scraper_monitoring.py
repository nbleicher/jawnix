from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from sqlalchemy import select

from jawnix.api import app
from jawnix.models import AuditEntry
from jawnix.scraper_monitoring import REGION_INTERVALS, REGIONS
from scraper_fake import (
    ScraperFake,
    SAMPLE,
    paused_activity,
)
from test_scraper_workspace import (  # noqa: F401 — fixtures
    enter_and_verify,
    workspace_client,
    workspace_settings,
)


def arm(fake: ScraperFake) -> ScraperFake:
    app.state.scraper_proxy_transport = httpx.MockTransport(fake)
    return fake


def monitoring(client, csrf):
    enter_and_verify(client, csrf)
    return client


# --- Every monitoring read -------------------------------------------------


def test_the_snapshot_carries_all_nine_regions_at_their_own_cadence(
    workspace_client,
):
    client, csrf, _, _ = workspace_client
    arm(ScraperFake())
    monitoring(client, csrf)

    body = client.get("/api/admin/scraper/monitoring").json()

    assert body["service_state"] == "connected"
    assert [region["region"] for region in body["regions"]] == list(REGIONS)
    assert all(region["state"] == "ok" for region in body["regions"])
    assert {
        region["region"]: region["refresh_seconds"] for region in body["regions"]
    } == REGION_INTERVALS


@pytest.mark.parametrize(
    ("region", "field", "probe"),
    [
        ("overall", "stack_status", lambda d: d["label"] == "Attention needed"),
        ("stack", "services", lambda d: len(d) == 6),
        ("stats", "stats", lambda d: d["businesses"] == 9_244_326),
        ("activity", "activity", lambda d: d["write_age"] == "2s"),
        ("log", "pipeline_events", lambda d: len(d) == 2),
        ("workers", "workers", lambda d: len(d) == 2),
        ("trends", "trends", lambda d: len(d) == 24),
        ("incidents", "incidents", lambda d: len(d) == 2),
        ("top-states", "top_states", lambda d: d[0]["state"] == "TX"),
    ],
)
def test_every_region_reads_independently(
    workspace_client,
    region,
    field,
    probe,
):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    monitoring(client, csrf)

    body = client.get(f"/api/admin/scraper/monitoring/{region}").json()

    assert body["region"] == region
    assert body["state"] == "ok"
    assert body["fetched_at"] is not None
    assert probe(body["data"][field])
    assert fake.calls[-1].url.path == f"/api/dashboard/{region}"


def test_stack_telemetry_keeps_every_host_number_the_dashboard_shows(
    workspace_client,
):
    """Parity: a value visible upstream must survive normalization."""

    client, csrf, _, _ = workspace_client
    arm(ScraperFake())
    monitoring(client, csrf)

    sample = client.get("/api/admin/scraper/monitoring/stack").json()["data"][
        "sample"
    ]

    for field, expected in SAMPLE.items():
        if field == "services":
            continue
        if field == "captured_at":
            # Same instant, spelled with Z rather than +00:00.
            assert datetime.fromisoformat(sample[field]) == (
                datetime.fromisoformat(expected)
            )
            continue
        assert sample[field] == expected, field


def test_raw_host_service_topology_is_never_forwarded(workspace_client):
    client, csrf, _, _ = workspace_client
    arm(ScraperFake())
    monitoring(client, csrf)

    response = client.get("/api/admin/scraper/monitoring/stack")

    assert "docker.service" in response.text  # the projected ServiceRow
    assert '"sub"' not in response.text  # the raw systemd blob
    assert response.json()["data"]["sample"].get("services") is None


def test_an_unknown_region_is_refused(workspace_client):
    client, csrf, _, _ = workspace_client
    arm(ScraperFake())
    monitoring(client, csrf)

    assert client.get("/api/admin/scraper/monitoring/nonsense").status_code == 422


# --- Partial failure, staleness, outage ------------------------------------


def test_one_failing_region_leaves_the_other_eight_readable(workspace_client):
    client, csrf, _, _ = workspace_client
    arm(ScraperFake(failing={"trends"}))
    monitoring(client, csrf)

    failed = client.get("/api/admin/scraper/monitoring/trends").json()
    assert failed["state"] == "unavailable"
    assert failed["data"] is None
    assert failed["refresh_seconds"] == REGION_INTERVALS["trends"]

    for region in REGIONS:
        if region == "trends":
            continue
        healthy = client.get(f"/api/admin/scraper/monitoring/{region}").json()
        assert healthy["state"] == "ok", region
        assert healthy["data"] is not None, region


def test_a_failing_region_is_reported_not_raised(workspace_client):
    """A dead panel must not read to the client as a broken request."""

    client, csrf, _, _ = workspace_client
    arm(ScraperFake(failing={"workers"}))
    monitoring(client, csrf)

    response = client.get("/api/admin/scraper/monitoring/workers")

    assert response.status_code == 200
    assert response.json()["state"] == "unavailable"


def test_a_full_outage_keeps_the_last_successful_connection(workspace_client):
    client, csrf, _, _ = workspace_client
    arm(ScraperFake())
    monitoring(client, csrf)
    assert client.get("/api/admin/scraper/monitoring").json()["service_state"] == (
        "connected"
    )

    arm(ScraperFake(offline=True))
    body = client.get("/api/admin/scraper/monitoring").json()

    assert body["service_state"] == "unavailable"
    assert body["last_successful_at"] is not None
    assert [region["region"] for region in body["regions"]] == list(REGIONS)
    assert all(region["state"] == "unavailable" for region in body["regions"])


def test_upstream_failure_never_leaks_its_body(workspace_client):
    client, csrf, _, _ = workspace_client
    arm(ScraperFake(failing={"stats"}))
    monitoring(client, csrf)

    response = client.get("/api/admin/scraper/monitoring/stats")

    assert "region unavailable" not in response.text


# --- The privileged session boundary ---------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/api/admin/scraper/monitoring", "/api/admin/scraper/monitoring/stats"],
)
def test_monitoring_requires_the_scraper_privileged_session(
    workspace_client,
    path,
):
    client, _, _, _ = workspace_client
    fake = arm(ScraperFake())

    response = client.get(path)

    assert response.status_code == 401
    assert fake.calls == []


def test_monitoring_stops_once_the_privileged_session_goes_idle(
    workspace_client,
):
    client, csrf, _, now = workspace_client
    arm(ScraperFake())
    monitoring(client, csrf)
    assert client.get("/api/admin/scraper/monitoring/stats").status_code == 200

    now[0] = now[0].replace(hour=now[0].hour + 1)

    assert client.get("/api/admin/scraper/monitoring/stats").status_code == 401


# --- Every pipeline write --------------------------------------------------


def _pause(client, csrf, **body):
    return client.post(
        "/api/admin/scraper/pipeline",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "Investigating source quality", **body},
    )


def test_pause_drains_the_queue_by_default(workspace_client, session):
    client, csrf, _, _ = workspace_client
    fake = arm(
        ScraperFake(activity_after_write=paused_activity(mode="drain"))
    )
    monitoring(client, csrf)

    body = _pause(client, csrf, action="pause").json()

    assert fake.writes == [b"action=pause&clear_queue=no"]
    assert body["pipeline_state"] == "paused"
    assert body["cancelled_jobs"] == 0
    assert body["region"]["data"]["pause_info"]["mode"] == "drain"
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_pipeline_paused"
        )
    ).one()
    assert entry.details["clearedQueue"] is False
    assert entry.details["cancelledJobs"] == 0


def test_pause_can_clear_the_queue_and_records_what_it_cancelled(
    workspace_client,
    session,
):
    client, csrf, _, _ = workspace_client
    fake = arm(
        ScraperFake(
            activity_after_write=paused_activity(cancelled_jobs=812, mode="clear")
        )
    )
    monitoring(client, csrf)

    body = _pause(client, csrf, action="pause", clear_queue=True).json()

    assert fake.writes == [b"action=pause&clear_queue=yes"]
    assert body["cancelled_jobs"] == 812
    assert body["region"]["data"]["activity"]["queue_depth"] == 0
    entry = session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_pipeline_queue_cleared"
        )
    ).one()
    assert entry.details["clearedQueue"] is True
    assert entry.details["cancelledJobs"] == 812
    assert entry.reason == "Investigating source quality"


def test_the_destructive_pause_is_its_own_audited_action(
    workspace_client,
    session,
):
    """Clearing the queue must be filterable in Activity, not hidden in a flag."""

    client, csrf, _, _ = workspace_client
    arm(ScraperFake(activity_after_write=paused_activity(812, "clear")))
    monitoring(client, csrf)

    _pause(client, csrf, action="pause", clear_queue=True)

    actions = set(
        session.scalars(
            select(AuditEntry.action).where(
                AuditEntry.target_type == "scraper_pipeline"
            )
        )
    )
    assert actions == {"scraper_pipeline_queue_cleared"}


def test_resume_restarts_the_pipeline(workspace_client, session):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    monitoring(client, csrf)

    body = _pause(client, csrf, action="resume").json()

    assert fake.writes == [b"action=resume"]
    assert body["pipeline_state"] == "running"
    assert session.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "scraper_pipeline_resumed"
        )
    ).one()


def test_a_resume_cannot_clear_the_queue(workspace_client):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    monitoring(client, csrf)

    response = _pause(client, csrf, action="resume", clear_queue=True)

    assert response.status_code == 422
    assert fake.writes == []


def test_a_pipeline_write_requires_a_reason(workspace_client):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    monitoring(client, csrf)

    response = client.post(
        "/api/admin/scraper/pipeline",
        headers={"X-CSRF-Token": csrf},
        json={"action": "pause"},
    )

    assert response.status_code == 422
    assert fake.writes == []


def test_a_pipeline_write_requires_the_privileged_session(workspace_client):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())

    response = _pause(client, csrf, action="pause")

    assert response.status_code == 401
    assert fake.writes == []


def test_a_pipeline_write_is_not_attempted_without_jawnix_csrf(
    workspace_client,
):
    client, csrf, _, _ = workspace_client
    fake = arm(ScraperFake())
    monitoring(client, csrf)

    response = client.post(
        "/api/admin/scraper/pipeline",
        json={"action": "pause", "reason": "Investigating source quality"},
    )

    assert response.status_code == 403
    assert fake.writes == []


def test_an_unreachable_scraper_records_no_pipeline_audit(
    workspace_client,
    session,
):
    client, csrf, _, _ = workspace_client
    arm(ScraperFake())
    monitoring(client, csrf)
    arm(ScraperFake(offline=True))

    response = _pause(client, csrf, action="pause")

    assert response.status_code == 503
    assert (
        session.scalars(
            select(AuditEntry).where(
                AuditEntry.target_type == "scraper_pipeline"
            )
        ).all()
        == []
    )


def test_null_pause_mode_is_accepted_like_the_real_dashboard():
    """The live dashboard sends ``"pause_info": {"mode": null}`` when nothing is
    paused. The fixture used ``""`` for that state, so every test passed while
    the real payload raised ValidationError and took the whole screen down —
    activity is part of the initial load, so one null broke all nine regions.

    Present-but-null is not the same as absent: the field default never applies.
    """
    from jawnix.scraper_monitoring import PauseInfo

    assert PauseInfo.model_validate({"mode": None, "cancelled_jobs": 0}).mode == ""
    assert PauseInfo.model_validate({"mode": None, "cancelled_jobs": None}).cancelled_jobs == 0
    # A real mode must still survive.
    assert PauseInfo.model_validate({"mode": "drain"}).mode == "drain"


def test_region_data_survives_a_null_pause_mode():
    """Guards the whole path, not just the model: this is the shape that 500'd."""
    from jawnix.scraper_monitoring import region_data

    payload = {
        "activity": {},
        "pipeline_state": {"key": "running", "label": "Running", "detail": ""},
        "pause_info": {"mode": None, "cancelled_jobs": 0},
    }

    assert region_data("activity", payload).pause_info.mode == ""
