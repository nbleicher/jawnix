from __future__ import annotations

import httpx

from jawnix.api import app
from jawnix.scraper_coverage import (
    STATE_CARDS_REFRESH_SECONDS,
    STATE_CELLS_REFRESH_SECONDS,
    STATE_KEYWORDS_REFRESH_SECONDS,
)
from scraper_fake import ScraperFake
from test_scraper_workspace import (  # noqa: F401 — shared fixtures
    enter_and_verify,
    workspace_client,
    workspace_settings,
)


def arm(fake: ScraperFake) -> ScraperFake:
    app.state.scraper_proxy_transport = httpx.MockTransport(fake)
    return fake


def verified(workspace_client):
    client, csrf, _, _ = workspace_client
    enter_and_verify(client, csrf)
    return client


def test_state_overview_preserves_live_counts_coverage_and_status(
    workspace_client,
):
    client = verified(workspace_client)
    fake = arm(ScraperFake())

    response = client.get("/api/admin/scraper/coverage")

    assert response.status_code == 200
    body = response.json()
    assert body["service_state"] == "connected"
    assert body["states"]["state"] == "ok"
    assert body["states"]["refresh_seconds"] == (
        STATE_CARDS_REFRESH_SECONDS
    )
    assert body["states"]["data"] == [
        {
            "state": "PA",
            "businesses": 161_863,
            "posted_cells": 110,
            "total_cells": 220,
            "active_keywords": 25,
            "coverage": 50,
            "status": "partial",
        },
        {
            "state": "OH",
            "businesses": 136_150,
            "posted_cells": 240,
            "total_cells": 240,
            "active_keywords": 25,
            "coverage": 100,
            "status": "covered",
        },
    ]
    assert fake.calls[-1].url.path == "/frag/states/cards"


def test_state_detail_keeps_every_keyword_field_and_grid_state(
    workspace_client,
):
    client = verified(workspace_client)
    fake = arm(ScraperFake())

    response = client.get("/api/admin/scraper/coverage/pa")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "PA"
    assert body["service_state"] == "connected"
    assert body["keywords"]["refresh_seconds"] == (
        STATE_KEYWORDS_REFRESH_SECONDS
    )
    assert body["cells"]["refresh_seconds"] == (
        STATE_CELLS_REFRESH_SECONDS
    )
    assert body["keywords"]["data"] == [
        {
            "keyword": "24 Hour Pharmacy",
            "businesses": 124,
            "posted_cells": 110,
            "total_cells": 220,
            "coverage": 50,
            "empty_rate": 0.125,
            "last_enqueued": "Jul 28, 11:59",
        },
        {
            "keyword": "Abatement Service",
            "businesses": 38,
            "posted_cells": 0,
            "total_cells": 220,
            "coverage": 0,
            "empty_rate": 0.75,
            "last_enqueued": None,
        },
    ]
    grid = body["cells"]["data"]
    assert {
        status: grid[status]
        for status in ("posted", "reserved", "failed", "uncovered")
    } == {
        "posted": 1,
        "reserved": 1,
        "failed": 1,
        "uncovered": 1,
    }
    assert [cell["status"] for cell in grid["cells"]] == [
        "posted",
        "reserved",
        "failed",
        "uncovered",
    ]
    assert grid["cells"][0] == {
        "index": 1,
        "cell": "40.000000,-80.000000",
        "status": "posted",
    }
    assert [call.url.path for call in fake.calls[-2:]] == [
        "/frag/states/pa/keywords",
        "/frag/states/pa/cells",
    ]


def test_keyword_and_grid_refreshes_remain_independently_addressable(
    workspace_client,
):
    client = verified(workspace_client)
    fake = arm(ScraperFake())

    keywords = client.get(
        "/api/admin/scraper/coverage/PA/keywords"
    ).json()
    cells = client.get("/api/admin/scraper/coverage/PA/cells").json()

    assert keywords["state"] == "ok"
    assert keywords["data"][0]["keyword"] == "24 Hour Pharmacy"
    assert cells["state"] == "ok"
    assert cells["data"]["failed"] == 1
    assert [call.url.path for call in fake.calls[-2:]] == [
        "/frag/states/pa/keywords",
        "/frag/states/pa/cells",
    ]


def test_one_fragment_failure_does_not_erase_the_other_region(
    workspace_client,
):
    client = verified(workspace_client)
    arm(ScraperFake(coverage_failing={"keywords"}))

    body = client.get("/api/admin/scraper/coverage/PA").json()

    assert body["service_state"] == "degraded"
    assert body["keywords"] == {
        "state": "unavailable",
        "refresh_seconds": STATE_KEYWORDS_REFRESH_SECONDS,
        "fetched_at": None,
        "data": None,
    }
    assert body["cells"]["state"] == "ok"
    assert body["cells"]["data"]["posted"] == 1


def test_coverage_outage_is_a_readable_contract_not_a_raw_error(
    workspace_client,
):
    client = verified(workspace_client)
    arm(ScraperFake(coverage_failing={"cards"}))

    response = client.get("/api/admin/scraper/coverage")

    assert response.status_code == 200
    assert response.json()["states"]["state"] == "unavailable"
    assert "cards unavailable" not in response.text


def test_successful_error_markup_is_not_mistaken_for_empty_coverage(
    workspace_client,
):
    client = verified(workspace_client)

    def unexpected_markup(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="<main>Temporarily unavailable</main>",
        )

    app.state.scraper_proxy_transport = httpx.MockTransport(
        unexpected_markup
    )

    overview = client.get("/api/admin/scraper/coverage")
    cells = client.get("/api/admin/scraper/coverage/PA/cells")

    assert overview.status_code == 200
    assert overview.json()["states"]["state"] == "unavailable"
    assert overview.json()["states"]["data"] is None
    assert cells.status_code == 200
    assert cells.json()["state"] == "unavailable"
    assert cells.json()["data"] is None


def test_an_unknown_state_is_refused_before_an_upstream_call(
    workspace_client,
):
    client = verified(workspace_client)
    fake = arm(ScraperFake())

    response = client.get("/api/admin/scraper/coverage/XX")

    assert response.status_code == 404
    assert fake.calls == []


def test_coverage_requires_the_privileged_scraper_session(workspace_client):
    client, _, _, _ = workspace_client
    fake = arm(ScraperFake())

    response = client.get("/api/admin/scraper/coverage")

    assert response.status_code == 401
    assert fake.calls == []
