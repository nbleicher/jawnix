from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jawnix.config import Settings
from jawnix.scraper_keywords import (
    KeywordGenerateRequest,
    KeywordRolloverRequest,
    KeywordSaveRequest,
    KeywordTextRequest,
    keyword_version,
)
from jawnix.scraper_database import (
    MultiStateExportRequest,
    StateExportRequest,
)
from jawnix.scraper_operations import (
    HTTPScraperOperations,
    ScraperOperationsError,
)
from scraper_fake import KEYWORDS, WINNERS


TOKEN = "test-scraper-control-token-0000000000000000"


def operations_settings(settings) -> Settings:
    return Settings(
        JAWNIX_BATCH_DIR=settings.batch_dir,
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET=settings.session_secret,
        JAWNIX_SCRAPER_OPS_URL="http://10.77.0.2:8090",
        JAWNIX_SCRAPER_CONTROL_TOKEN=TOKEN,
        JAWNIX_SCRAPER_OPS_TIMEOUT_SECONDS=1,
        JAWNIX_SCRAPER_OPS_GENERATION_TIMEOUT_SECONDS=9,
    )


def test_http_adapter_speaks_the_typed_keyword_contract(settings):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/keywords":
            return httpx.Response(
                200,
                json={
                    "current": KEYWORDS,
                    "version": keyword_version(KEYWORDS),
                    "ai_enabled": True,
                    "rollover": {
                        "enabled": False,
                        "state": "off",
                        "label": "Off",
                        "detail": "Manual keyword batches",
                        "percent_complete": 60,
                        "posted_jobs": 12,
                        "expected_jobs": 20,
                        "last_status": "generated",
                        "last_event": "2026-07-28T12:00:00+00:00",
                    },
                    "winners": [
                        {**WINNERS[0], "rank": 1, "last_used": "2026-07-28"}
                    ],
                },
            )
        if path == "/api/keywords/winners":
            return httpx.Response(
                200,
                json={"winners": [{**WINNERS[0], "rank": 1}]},
            )
        if path == "/api/keywords/preview":
            return httpx.Response(
                200,
                json={
                    "proposed": ["plumbers", "roofers"],
                    "added": ["roofers"],
                    "removed": ["electricians"],
                    "unchanged": ["plumbers"],
                    "expected_version": keyword_version(KEYWORDS),
                },
            )
        if path == "/api/keywords/save":
            proposed = ["plumbers", "roofers"]
            return httpx.Response(
                200,
                json={
                    "saved": True,
                    "enqueued": True,
                    "current": proposed,
                    "version": keyword_version(proposed),
                    "diff": {
                        "proposed": proposed,
                        "added": ["roofers"],
                        "removed": ["electricians"],
                        "unchanged": ["plumbers"],
                        "expected_version": keyword_version(KEYWORDS),
                    },
                },
            )
        if path == "/api/keywords/generate":
            return httpx.Response(
                200,
                json={
                    "generation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "mode": "broad",
                    "seed_keyword": None,
                    "keywords": ["Generated niche"],
                    "excluded_count": 3,
                    "notice": "Review below; nothing has been saved or enqueued.",
                },
            )
        if path == "/api/keywords/rollover":
            return httpx.Response(
                200,
                json={
                    "enabled": True,
                    "state": "working",
                    "label": "Current batch active",
                    "detail": "12 of 20 coverage jobs enqueued",
                    "percent_complete": 60,
                    "posted_jobs": 12,
                    "expected_jobs": 20,
                },
            )
        raise AssertionError(path)

    adapter = HTTPScraperOperations(
        operations_settings(settings),
        transport=httpx.MockTransport(handler),
    )

    async def exercise():
        workspace = await adapter.list_keywords()
        winners = await adapter.keyword_winners()
        preview = await adapter.preview_keywords(
            KeywordTextRequest(text="plumbers\nroofers")
        )
        saved = await adapter.save_keywords(
            KeywordSaveRequest(
                text="plumbers\nroofers",
                expected_version=keyword_version(KEYWORDS),
                review_token="jawnix-only-review-token",
                enqueue=True,
            )
        )
        generated = await adapter.generate_keywords(KeywordGenerateRequest())
        rollover = await adapter.set_keyword_rollover(
            KeywordRolloverRequest(action="enable")
        )
        return workspace, winners, preview, saved, generated, rollover

    workspace, winners, preview, saved, generated, rollover = asyncio.run(
        exercise()
    )

    assert workspace.rollover.last_event == "Jul 28 · 12:00 UTC"
    assert workspace.winners[0].last_used == "Jul 28"
    assert winners[0].keyword == "plumbers"
    assert preview.added == ["roofers"]
    assert saved.enqueued is True
    assert generated.excluded_count == 3
    assert rollover.enabled is True
    assert [call.url.path for call in calls] == [
        "/api/keywords",
        "/api/keywords/winners",
        "/api/keywords/preview",
        "/api/keywords/save",
        "/api/keywords/generate",
        "/api/keywords/rollover",
    ]
    assert all(call.headers["authorization"] == f"Bearer {TOKEN}" for call in calls)
    save_body = json.loads(calls[3].content)
    assert save_body == {
        "text": "plumbers\nroofers",
        "expected_version": keyword_version(KEYWORDS),
        "enqueue": True,
        "generation_id": None,
    }
    assert "review_token" not in save_body
    assert calls[4].extensions["timeout"]["read"] == 9
    assert calls[2].extensions["timeout"]["read"] == 1


def test_http_adapter_speaks_the_typed_database_and_coverage_contract(
    settings,
):
    calls: list[httpx.Request] = []

    database_workspace = {
        "totals": {"businesses": 5, "unique_phones": 4},
        "states": [
            {
                "state": "OH",
                "businesses": 5,
                "unique_phones": 4,
                "niches": 2,
            }
        ],
        "browse": {
            "records": [
                {
                    "title": "",
                    "phone": "6145550101",
                    "website": "https://buckeye.example",
                    "state": "OH",
                    "niche": "plumbers",
                    "last_seen": "2026-07-28T11:59:00+00:00",
                }
            ],
            "search": "plumbing",
            "state": "OH",
            "page": 1,
            "page_size": 50,
            "total": 1,
            "pages": 1,
            "has_previous": False,
            "has_next": False,
        },
        "stored_exports": [{"filename": "OH.csv", "size_label": "1.0 KB"}],
    }
    database_state = {
        "state": "OH",
        "totals": {
            "state": "OH",
            "businesses": 5,
            "unique_phones": 4,
            "niches": 1,
        },
        "niches": [
            {
                "key": "plumbers",
                "label": "plumbers",
                "businesses": 5,
                "unique_phones": 4,
            }
        ],
    }
    keywords = [
        {
            "keyword": "Plumbers",
            "businesses": 5,
            "posted_cells": 1,
            "total_cells": 2,
            "coverage": 50,
            "empty_rate": 0.25,
            "last_enqueued": "2026-07-28T11:59:00+00:00",
        }
    ]
    cells = {
        "cells": [
            {"index": 1, "cell": "40.0,-80.0", "status": "posted"}
        ],
        "posted": 1,
        "reserved": 0,
        "failed": 0,
        "uncovered": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/database":
            return httpx.Response(200, json=database_workspace)
        if path == "/api/database/states/oh":
            return httpx.Response(200, json=database_state)
        if path in {
            "/api/database/exports/state/oh",
            "/api/database/exports/states",
        }:
            filename = (
                "OH-all-phone-leads-2026-07-29.csv"
                if path.endswith("/state/oh")
                else "OH-PA-phone-leads-2026-07-29.csv"
            )
            return httpx.Response(
                200,
                json={
                    "filename": filename,
                    "media_type": "text/csv",
                    "content": "business_name,phone_number,state\n",
                },
            )
        if path == "/api/database/exports/stored/OH.csv":
            return httpx.Response(
                200,
                json={
                    "filename": "OH.csv",
                    "media_type": "text/csv",
                    "content": "phone,title\n",
                },
            )
        if path == "/api/database/exports/oh/regenerate":
            return httpx.Response(
                200,
                json={
                    "generated": "OH.csv",
                    "stored_exports": [
                        {"filename": "OH.csv", "size_label": "1.0 KB"}
                    ],
                },
            )
        if path == "/api/coverage":
            return httpx.Response(
                200,
                json={
                    "states": [
                        {
                            "state": "OH",
                            "businesses": 5,
                            "posted_cells": 1,
                            "total_cells": 2,
                            "active_keywords": 1,
                            "coverage": 50,
                            "status": "partial",
                        }
                    ]
                },
            )
        if path == "/api/coverage/oh":
            return httpx.Response(
                200,
                json={"state": "OH", "keywords": keywords, "cells": cells},
            )
        if path == "/api/coverage/oh/keywords":
            return httpx.Response(
                200,
                json={"state": "OH", "keywords": keywords},
            )
        if path == "/api/coverage/oh/cells":
            return httpx.Response(200, json=cells)
        raise AssertionError(path)

    adapter = HTTPScraperOperations(
        operations_settings(settings),
        transport=httpx.MockTransport(handler),
    )

    async def exercise():
        return (
            await adapter.database_workspace(
                search="plumbing", state="oh", page=1
            ),
            await adapter.database_state("oh"),
            await adapter.export_database_state(
                "oh", StateExportRequest(niches=None)
            ),
            await adapter.export_database_states(
                MultiStateExportRequest(states=["oh", "pa"])
            ),
            await adapter.stored_database_export("OH.csv"),
            await adapter.regenerate_database_exports("oh"),
            await adapter.coverage_states(),
            await adapter.coverage_state("oh"),
            await adapter.coverage_state_keywords("oh"),
            await adapter.coverage_state_cells("oh"),
        )

    results = asyncio.run(exercise())

    assert results[0].browse.records[0].last_seen == "Jul 28, 11:59"
    assert results[0].browse.records[0].title == "Untitled"
    assert results[1].niches[0].key == "plumbers"
    assert results[2].filename == "OH-all-phone-leads-2026-07-29.csv"
    assert results[3].filename == "OH-PA-phone-leads-2026-07-29.csv"
    assert results[4].content == "phone,title\n"
    assert results[5].generated == "OH.csv"
    assert results[6][0].status == "partial"
    assert results[7].keywords[0].last_enqueued == "Jul 28, 11:59"
    assert results[8].keywords[0].keyword == "Plumbers"
    assert results[9].posted == 1
    assert dict(calls[0].url.params) == {
        "search": "plumbing",
        "state": "oh",
        "page": "1",
    }
    assert json.loads(calls[2].content) == {"niches": None}
    assert json.loads(calls[3].content) == {"states": ["oh", "pa"]}
    assert all(call.headers["authorization"] == f"Bearer {TOKEN}" for call in calls)


def test_http_adapter_preserves_declared_errors_and_redacts_transport(
    settings,
    caplog,
):
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if request.url.path.endswith("preview"):
            return httpx.Response(
                422,
                json={"detail": "At least one keyword is required."},
            )
        raise httpx.ReadTimeout("secret upstream timeout", request=request)

    adapter = HTTPScraperOperations(
        operations_settings(settings),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ScraperOperationsError) as declared:
        asyncio.run(
            adapter.preview_keywords(KeywordTextRequest(text=""))
        )
    assert declared.value.status_code == 422
    assert declared.value.detail == "At least one keyword is required."

    with pytest.raises(ScraperOperationsError) as unavailable:
        asyncio.run(adapter.generate_keywords(KeywordGenerateRequest()))
    assert unavailable.value.transport_error == "ReadTimeout"
    assert requests == 2
    assert "path=/api/keywords/generate" in caplog.text
    assert "transport_error=ReadTimeout" in caplog.text
    assert TOKEN not in caplog.text
    assert "10.77.0.2" not in caplog.text
    assert "secret upstream timeout" not in caplog.text


def test_http_adapter_does_not_request_without_runtime_configuration(settings):
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={})

    adapter = HTTPScraperOperations(
        settings,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ScraperOperationsError):
        asyncio.run(adapter.list_keywords())
    assert requests == 0
