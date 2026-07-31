from __future__ import annotations

import hashlib
import json
from pathlib import Path

import asyncpg
import pytest
import yaml
from app.contracts import (
    AdjacentKeywordResponse,
    CampaignHistory,
    CoverageStates,
    DashboardSnapshot,
    DatabaseExport,
    DatabaseStateDetail,
    DatabaseWorkspace,
    DatasetPublication,
    ExportRegeneration,
    Health,
    KeywordDiff,
    KeywordGenerationDraft,
    KeywordRollover,
    KeywordSaveResult,
    KeywordWinners,
    KeywordWorkspace,
    NicheProposalResponse,
    PipelineControlResult,
    RuntimePreview,
    RuntimeSaveResult,
    RuntimeWorkspace,
    SourceSegments,
    StateCoverageDetail,
    StateGridCoverage,
    StateKeywords,
    WorkspaceSummary,
)
from app.keyword_generator import GenerationResult
from app.main import app

TOKEN = "test-scraper-control-token-0000000000000000"
EXPECTED_ROUTES = {
    ("GET", "/healthz"),
    ("GET", "/api/workspace"),
    ("GET", "/api/keywords"),
    ("GET", "/api/keywords/winners"),
    ("POST", "/api/keywords/preview"),
    ("POST", "/api/keywords/save"),
    ("POST", "/api/keywords/generate"),
    ("POST", "/api/keywords/rollover"),
    ("GET", "/api/database"),
    ("GET", "/api/database/states/{state}"),
    ("POST", "/api/database/exports/state/{state}"),
    ("POST", "/api/database/exports/states"),
    ("GET", "/api/database/exports/stored/{filename}"),
    ("POST", "/api/database/exports/{state}/regenerate"),
    ("GET", "/api/coverage"),
    ("GET", "/api/coverage/{state}"),
    ("GET", "/api/coverage/{state}/keywords"),
    ("GET", "/api/coverage/{state}/cells"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/dashboard/{region}"),
    ("POST", "/api/pipeline"),
    ("GET", "/api/runtime"),
    ("POST", "/api/runtime/preview"),
    ("POST", "/api/runtime/save"),
    ("GET", "/api/history"),
    ("GET", "/api/source-segments"),
    ("POST", "/api/source-segments/activate"),
    ("GET", "/api/source-segments/publication"),
    ("POST", "/api/source-segments/niche-proposals"),
    ("POST", "/api/source-segments/adjacent-keywords"),
}


def assert_contract(response, model):
    assert response.status_code == 200, response.text
    return model.model_validate(response.json())


def test_compose_exposes_only_the_wireguard_control_process():
    root = Path(__file__).parents[2]
    compose = yaml.safe_load((root / "docker-compose.box.yml").read_text())
    control = compose["services"]["scraper-control"]

    assert control["environment"]["JAWNIX_SCRAPER_CONTROL_TOKEN"].startswith("$")
    assert control["ports"] == [
        "${SCRAPER_CONTROL_BIND_ADDRESS:?set SCRAPER_CONTROL_BIND_ADDRESS}:8090:8000"
    ]
    assert "dashboard" not in compose["services"]


def test_contract_inventory_is_explicit_and_complete():
    actual = {
        (method, route.path)
        for route in app.routes
        for method in route.methods or set()
    }
    assert actual == EXPECTED_ROUTES


@pytest.mark.asyncio
async def test_every_request_requires_the_bearer_token(app_client, caplog):
    client, _ = app_client
    for path in ("/healthz", "/api/workspace", "/api/not-a-route"):
        response = await client.get(path, headers={"Authorization": ""})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    assert (
        await client.get(
            "/api/workspace",
            headers={"Authorization": "Basic operator:secret"},
        )
    ).status_code == 401
    assert TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_every_control_endpoint_matches_the_shared_contract(
    app_client, monkeypatch
):
    client, settings = app_client

    async def generate(_self, mode, _excluded, seed=None):
        prefix = "Adjacent" if mode == "adjacent" else "Generated"
        return GenerationResult(
            [f"{prefix} Niche {index}" for index in range(1, 26)],
            4,
        )

    async def propose_niches(_self, segments):
        return [{"id": item["id"], "niche": "Home Services"} for item in segments]

    monkeypatch.setattr(
        "app.keyword_generator.KeywordGenerator.generate", generate
    )
    monkeypatch.setattr(
        "app.keyword_generator.KeywordGenerator.propose_niches", propose_niches
    )

    assert_contract(await client.get("/healthz"), Health)
    workspace = assert_contract(
        await client.get("/api/workspace"), WorkspaceSummary
    )
    assert workspace.business_count == 5

    keywords = assert_contract(
        await client.get("/api/keywords"), KeywordWorkspace
    )
    assert keywords.current == ["plumbers", "electricians"]
    winners = assert_contract(
        await client.get("/api/keywords/winners"), KeywordWinners
    )
    assert winners.winners[0].keyword == "plumbers"
    preview = assert_contract(
        await client.post(
            "/api/keywords/preview",
            json={"text": "Plumbers\nroofers\nROOFERS\n# ignored\n"},
        ),
        KeywordDiff,
    )
    assert preview.added == ["roofers"]
    generated = assert_contract(
        await client.post(
            "/api/keywords/generate", json={"mode": "broad"}
        ),
        KeywordGenerationDraft,
    )
    assert len(generated.keywords) == 25
    saved = assert_contract(
        await client.post(
            "/api/keywords/save",
            json={
                "text": "Plumbers\nroofers\n",
                "expected_version": preview.expected_version,
                "enqueue": True,
                "generation_id": generated.generation_id,
            },
        ),
        KeywordSaveResult,
    )
    assert saved.current == ["Plumbers", "roofers"]
    assert settings.enqueue_trigger_path.exists()
    rollover = assert_contract(
        await client.post(
            "/api/keywords/rollover", json={"action": "enable"}
        ),
        KeywordRollover,
    )
    assert rollover.enabled is True
    assert_contract(
        await client.post(
            "/api/keywords/rollover", json={"action": "disable"}
        ),
        KeywordRollover,
    )

    database = assert_contract(
        await client.get("/api/database"), DatabaseWorkspace
    )
    assert database.totals.businesses == 5
    assert_contract(
        await client.get("/api/database/states/oh"), DatabaseStateDetail
    )
    state_export = assert_contract(
        await client.post(
            "/api/database/exports/state/oh", json={"niches": None}
        ),
        DatabaseExport,
    )
    assert "6145550101" in state_export.content
    multi_export = assert_contract(
        await client.post(
            "/api/database/exports/states", json={"states": ["oh", "ky"]}
        ),
        DatabaseExport,
    )
    assert "Bluegrass Plumbing" in multi_export.content
    regenerated = assert_contract(
        await client.post("/api/database/exports/oh/regenerate"),
        ExportRegeneration,
    )
    assert regenerated.generated == "OH.csv"
    stored = assert_contract(
        await client.get("/api/database/exports/stored/OH.csv"),
        DatabaseExport,
    )
    assert stored.content.startswith("phone,title")

    coverage = assert_contract(
        await client.get("/api/coverage"), CoverageStates
    )
    assert any(item.state == "OH" for item in coverage.states)
    assert_contract(
        await client.get("/api/coverage/oh"), StateCoverageDetail
    )
    assert_contract(
        await client.get("/api/coverage/oh/keywords"), StateKeywords
    )
    assert_contract(
        await client.get("/api/coverage/oh/cells"), StateGridCoverage
    )

    dashboard = assert_contract(
        await client.get("/api/dashboard"), DashboardSnapshot
    )
    assert dashboard.stats.businesses == 5
    for region in (
        "overall",
        "stack",
        "stats",
        "activity",
        "log",
        "workers",
        "trends",
        "incidents",
        "top-states",
    ):
        assert_contract(
            await client.get(f"/api/dashboard/{region}"),
            DashboardSnapshot,
        )

    pause = assert_contract(
        await client.post(
            "/api/pipeline",
            json={"action": "pause", "clear_queue": False},
        ),
        PipelineControlResult,
    )
    assert pause.pause_info.mode == "drain"
    clear = assert_contract(
        await client.post(
            "/api/pipeline",
            json={"action": "pause", "clear_queue": True},
        ),
        PipelineControlResult,
    )
    assert clear.cancelled_jobs == 1
    assert_contract(
        await client.post(
            "/api/pipeline",
            json={"action": "resume", "clear_queue": False},
        ),
        PipelineControlResult,
    )

    runtime = assert_contract(
        await client.get("/api/runtime"), RuntimeWorkspace
    )
    runtime_payload = runtime.current.model_dump(mode="json")
    runtime_preview = assert_contract(
        await client.post(
            "/api/runtime/preview", json={"configuration": runtime_payload}
        ),
        RuntimePreview,
    )
    assert_contract(
        await client.post(
            "/api/runtime/save",
            json={
                "configuration": runtime_payload,
                "expected_version": runtime_preview.expected_version,
                "enqueue": False,
            },
        ),
        RuntimeSaveResult,
    )
    history = assert_contract(
        await client.get("/api/history"), CampaignHistory
    )
    assert any(item.keyword == "plumbers" for item in history.rows)

    segments = assert_contract(
        await client.get("/api/source-segments"), SourceSegments
    )
    next_segments = [
        {
            "id": item.id,
            "keyword": item.keyword,
            "state": item.state,
            "niche": item.niche,
            "niche_confirmed": item.niche_confirmed,
            "status": item.status,
            "cadence_multiplier": item.cadence_multiplier,
            "seed_segment_id": item.seed_segment_id,
        }
        for item in segments.segments
    ]
    version = segments.version + 1
    checksum_segments = [
        {**item, "version": version} for item in next_segments
    ]
    checksum = hashlib.sha256(
        json.dumps(
            {"version": version, "segments": checksum_segments},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    activated = assert_contract(
        await client.post(
            "/api/source-segments/activate",
            json={
                "version": version,
                "checksum": checksum,
                "segments": next_segments,
            },
        ),
        SourceSegments,
    )
    assert activated.scheduled is True
    publication = assert_contract(
        await client.get("/api/source-segments/publication"),
        DatasetPublication,
    )
    assert publication.status == "committed"
    proposals = assert_contract(
        await client.post(
            "/api/source-segments/niche-proposals",
            json={
                "segments": [
                    {
                        "id": activated.segments[0].id,
                        "keyword": activated.segments[0].keyword,
                        "state": activated.segments[0].state,
                    }
                ]
            },
        ),
        NicheProposalResponse,
    )
    assert proposals.proposals[0].niche == "Home Services"
    adjacent = assert_contract(
        await client.post(
            "/api/source-segments/adjacent-keywords",
            json={
                "seed_keyword": "plumbers",
                "excluded_keywords": ["plumbers"],
                "count": 3,
            },
        ),
        AdjacentKeywordResponse,
    )
    assert len(adjacent.keywords) == 3

    connection = await asyncpg.connect(settings.database_url)
    try:
        await connection.execute(
            "UPDATE river_job SET state='available', finalized_at=NULL WHERE id=101"
        )
    finally:
        await connection.close()
