import csv
import io
from pathlib import Path

import asyncpg
import pytest
import yaml


def test_dashboard_keywords_use_writable_control_mount():
    root = Path(__file__).parents[2]
    compose = yaml.safe_load((root / "docker-compose.box.yml").read_text())
    dashboard = compose["services"]["dashboard"]

    assert dashboard["environment"]["KEYWORDS_PATH"] == "/app/control/runtime/keywords.txt"
    assert "./control:/app/control" in dashboard["volumes"]
    assert all(not volume.endswith(":/app/keywords.txt") for volume in dashboard["volumes"])


@pytest.mark.asyncio
async def test_auth_and_health(app_client):
    client, _ = app_client
    assert (await client.get("/healthz")).status_code == 200
    response = await client.get("/dashboard", auth=None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


@pytest.mark.asyncio
async def test_dashboard_seeded_metrics(app_client):
    client, _ = app_client
    response = await client.get("/dashboard")
    assert response.status_code == 200
    for text in ("Operational", "PostgreSQL", "Performance trends", "5", "3", "2", "33.3%", "worker-1"):
        assert text in response.text
    assert "worker-stale" not in response.text
    assert "Live database activity" in response.text
    assert "Phone leads" in response.text
    assert "4 businesses with phone" in response.text
    assert "last hour" in response.text
    assert "24h projection" in response.text
    assert "empty-adjusted" in response.text
    assert "productive /" not in response.text
    assert "Database activity log" in response.text
    assert "tail -f pipeline.log" in response.text
    assert "job#102" in response.text
    assert "+3 phones · 8 raw" in response.text
    assert "job#101" not in response.text
    assert "Running" in response.text
    assert "OH" in response.text
    assert "electricians" in response.text
    assert (await client.get("/frag/dashboard/stack")).status_code == 200
    assert (await client.get("/frag/dashboard/activity")).status_code == 200
    log = await client.get("/frag/dashboard/log")
    assert log.status_code == 200
    assert "PHONE" in log.text
    assert "electricians" in log.text
    assert "job#101" not in log.text
    assert (await client.get("/frag/dashboard/trends")).status_code == 200
    assert (await client.get("/frag/dashboard/incidents")).status_code == 200


@pytest.mark.asyncio
async def test_pipeline_pause_and_resume(app_client):
    client, settings = app_client
    page = await client.get("/dashboard")
    assert "Clear queued jobs?" in page.text
    assert "No, keep queue" in page.text
    assert "Yes, clear queue" in page.text

    pause = await client.post(
        "/dashboard/pipeline", data={"action": "pause", "clear_queue": "no"},
    )
    assert pause.status_code == 200
    assert settings.pipeline_pause_path.exists()
    assert '"mode": "drain"' in settings.pipeline_pause_path.read_text()
    assert "Pausing" in pause.text
    assert "Resume" in pause.text

    resume = await client.post("/dashboard/pipeline", data={"action": "resume"})
    assert resume.status_code == 200
    assert not settings.pipeline_pause_path.exists()
    assert "Running" in resume.text
    assert "Pause" in resume.text

    clear = await client.post(
        "/dashboard/pipeline", data={"action": "pause", "clear_queue": "yes"},
    )
    assert clear.status_code == 200
    assert "Queue cleared (1 cancelled)" in clear.text
    assert '"mode": "clear"' in settings.pipeline_pause_path.read_text()
    connection = await asyncpg.connect(settings.database_url)
    try:
        assert await connection.fetchval("SELECT state::text FROM river_job WHERE id=101") == "cancelled"
        assert await connection.fetchval("SELECT state::text FROM river_job WHERE id=102") == "running"
        assert await connection.fetchval("SELECT state::text FROM river_job WHERE id=103") == "available"
        await connection.execute(
            "UPDATE river_job SET state='available', finalized_at=NULL WHERE id=101"
        )
    finally:
        await connection.close()
    await client.post("/dashboard/pipeline", data={"action": "resume"})

    invalid = await client.post("/dashboard/pipeline", data={"action": "stop"})
    assert invalid.status_code == 422
    invalid_clear = await client.post(
        "/dashboard/pipeline", data={"action": "pause", "clear_queue": "all"},
    )
    assert invalid_clear.status_code == 422
    unauthorized = await client.post(
        "/dashboard/pipeline", data={"action": "pause"}, auth=None,
    )
    assert unauthorized.status_code == 401


@pytest.mark.asyncio
async def test_states_and_unknown_state(app_client):
    client, _ = app_client
    response = await client.get("/states")
    assert response.status_code == 200
    assert "OH" in response.text
    detail = await client.get("/states/oh")
    assert detail.status_code == 200
    assert "plumbers" in detail.text
    assert (await client.get("/states/zz")).status_code == 404


@pytest.mark.asyncio
async def test_keyword_preview_save_and_trigger(app_client):
    client, settings = app_client
    page = await client.get("/keywords")
    assert "Automatic batches" in page.text
    assert "Manual keyword batches" in page.text
    enabled = await client.post("/keywords/auto-rollover", data={"action": "enable"})
    assert enabled.status_code == 200
    assert "Current batch active" in enabled.text
    assert "Disable" in enabled.text
    disabled = await client.post("/keywords/auto-rollover", data={"action": "disable"})
    assert disabled.status_code == 200
    assert "Manual keyword batches" in disabled.text
    assert (await client.get("/frag/keywords/auto-rollover")).status_code == 200
    assert (await client.post(
        "/keywords/auto-rollover", data={"action": "enable"}, auth=None,
    )).status_code == 401
    assert 'id="toast-region"' in page.text
    assert "Generating 25 unused keywords..." in page.text
    assert "Saving keyword list..." in page.text
    preview = await client.post("/keywords/preview", data={"text": " Plumbers \nroofers\nROOFERS\n#skip\n"})
    assert preview.status_code == 200
    assert "roofers" in preview.text
    assert "electricians" in preview.text
    saved = await client.post("/keywords/save", data={"text": "Plumbers\nroofers\nROOFERS\n", "enqueue": "true"})
    assert saved.status_code == 200
    assert settings.keywords_path.read_text() == "Plumbers\nroofers\n"
    assert settings.enqueue_trigger_path.exists()
    assert list(settings.keywords_path.parent.glob("keywords.txt.bak.*"))
    uploaded = await client.post(
        "/keywords/save",
        data={"text": "ignored"},
        files={"upload": ("keywords.txt", b"hvac\nHVAC\n", "text/plain")},
    )
    assert uploaded.status_code == 200
    assert settings.keywords_path.read_text() == "hvac\n"


@pytest.mark.asyncio
async def test_history_sort_injection_falls_back(app_client):
    client, _ = app_client
    response = await client.get("/history", params={"sort": "last_enqueued;drop table leads", "direction": "desc"})
    assert response.status_code == 200
    assert "plumbers" in response.text


@pytest.mark.asyncio
async def test_configure_invalid_then_valid_save(app_client):
    client, settings = app_client
    before = settings.active_states_path.read_text()
    invalid = await client.post("/configure/save", data={"states": "zz", "zoom": "15"})
    assert invalid.status_code == 422
    assert settings.active_states_path.read_text() == before
    valid = await client.post(
        "/configure/save",
        data={
            "states": ["oh", "ky"], "zoom": "15", "radius": "10000",
            "depth": "3", "lang": "en", "timeout": "300", "target_depth": "1000",
            "batch_size": "100", "poll_secs": "30", "skip_recent_days": "0",
            "cell_size_km_oh": "30",
        },
    )
    assert valid.status_code == 200
    config = yaml.safe_load(settings.active_states_path.read_text())
    assert config["states"] == ["oh", "ky"]
    assert config["overrides"]["oh"]["cell_size_km"] == 30


@pytest.mark.asyncio
async def test_database_matrix_browse_export_and_download_guard(app_client):
    client, settings = app_client
    response = await client.get("/database")
    assert response.status_code == 200
    assert "Buckeye Plumbing" in response.text
    assert "Download by state" in response.text
    assert "total businesses" in response.text
    assert 'href="/database/states/oh"' in response.text
    assert "State × niche" not in response.text

    detail = await client.get("/database/states/oh")
    assert detail.status_code == 200
    assert "OH database" in detail.text
    assert "Download entire state" in detail.text
    assert "Download selected" in detail.text
    assert "Select all" in detail.text
    assert "plumbers" in detail.text

    state_download = await client.get("/database/states/oh/download", params={"scope": "all"})
    assert state_download.status_code == 200
    assert state_download.headers["content-type"].startswith("text/csv")
    assert "OH-all-phone-leads-" in state_download.headers["content-disposition"]
    state_rows = list(csv.reader(io.StringIO(state_download.text)))
    assert state_rows[0] == ["business_name", "phone_number", "state"]
    assert {row[1] for row in state_rows[1:]} == {"6145550101", "2165550102"}
    assert any(row[0] == "Capital Electric" and row[1] == "6145550101" for row in state_rows)

    niche_download = await client.get(
        "/database/states/oh/download", params={"scope": "selected", "keyword": "plumbers"},
    )
    niche_rows = list(csv.reader(io.StringIO(niche_download.text)))
    assert niche_download.status_code == 200
    assert "OH-plumbers-phone-leads-" in niche_download.headers["content-disposition"]
    assert any(row[0] == "Buckeye Plumbing" and row[1] == "6145550101" for row in niche_rows)

    combined = await client.get(
        "/database/states/oh/download",
        params=[("scope", "selected"), ("keyword", "plumbers"), ("keyword", "electricians")],
    )
    combined_rows = list(csv.reader(io.StringIO(combined.text)))
    assert combined.status_code == 200
    assert "OH-2-niches-phone-leads-" in combined.headers["content-disposition"]
    assert len(combined_rows) == 3
    assert any(row[0] == "Capital Electric" and row[1] == "6145550101" for row in combined_rows)

    bulk = await client.get(
        "/database/bulk-download", params=[("state", "oh"), ("state", "ky")],
    )
    assert bulk.status_code == 200
    assert bulk.headers["content-type"].startswith("text/csv")
    assert "OH-KY-phone-leads-" in bulk.headers["content-disposition"]
    bulk_rows = list(csv.reader(io.StringIO(bulk.text)))
    assert bulk_rows[0] == ["business_name", "phone_number", "state"]
    assert sum(row[0] == "business_name" for row in bulk_rows) == 1
    assert {row[2] for row in bulk_rows[1:]} == {"OH", "KY"}
    assert any(row[0] == "Bluegrass Plumbing" and row[2] == "KY" for row in bulk_rows)
    assert (await client.get("/database/bulk-download")).status_code == 422
    assert (await client.get(
        "/database/bulk-download", params={"state": "zz"},
    )).status_code == 404

    assert (await client.get("/database/states/zz")).status_code == 404
    assert (await client.get(
        "/database/states/oh/download", params={"scope": "selected"},
    )).status_code == 422
    assert (await client.get(
        "/database/states/oh/download", params={"scope": "selected", "keyword": "not-a-niche"},
    )).status_code == 400
    assert (await client.get(
        "/database/states/oh/download", params={"scope": "all"}, auth=None,
    )).status_code == 401

    export = await client.post("/database/export/oh")
    assert export.status_code == 200
    path = settings.exports_dir / "OH.csv"
    assert path.read_text().splitlines()[0] == "phone,title"
    download = await client.get("/database/download/OH.csv")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    traversal = await client.get("/database/download/%2E%2E%2Fetc%2Fpasswd")
    assert traversal.status_code in (400, 404)


@pytest.mark.asyncio
async def test_database_uncategorized_normalization_and_inactive_state(app_client):
    client, settings = app_client
    connection = await asyncpg.connect(settings.database_url)
    try:
        await connection.execute(
            """
            INSERT INTO businesses
              (dedup_key,title,phone,state,keyword,last_seen)
            VALUES
              ('test:uncategorized','Quoted, "Company"','+1 (937) 555-0188','oh',NULL,now()+interval '1 minute'),
              ('test:invalid-phone','Invalid Phone','555-0199','oh',NULL,now()+interval '2 minutes'),
              ('test:inactive-state','Inactive State Co','717-555-0199','pa','specialty services',now())
            """
        )

        page = await client.get("/database")
        assert page.status_code == 200
        assert 'href="/database/states/pa"' in page.text
        assert '<option value="pa"' in page.text

        detail = await client.get("/database/states/oh")
        assert "Uncategorized" in detail.text
        uncategorized = await client.get(
            "/database/states/oh/download",
            params={"scope": "selected", "keyword": "__uncategorized__"},
        )
        assert uncategorized.status_code == 200
        assert "OH-uncategorized-phone-leads-" in uncategorized.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(uncategorized.text)))
        assert rows == [
            ["business_name", "phone_number", "state"],
            ['Quoted, "Company"', "9375550188", "OH"],
        ]
    finally:
        await connection.execute("DELETE FROM businesses WHERE dedup_key LIKE 'test:%'")
        await connection.close()
