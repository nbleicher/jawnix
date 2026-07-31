import os
import shutil
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio

os.environ.setdefault(
    "JAWNIX_SCRAPER_CONTROL_TOKEN",
    "test-scraper-control-token-0000000000000000",
)

from app.config import Settings
from app.main import create_app

TESTS = Path(__file__).resolve().parent
SCALE = TESTS.parent.parent
MIGRATIONS = SCALE / "scraper_changes" / "migrations"


def migration_up_sql(path: Path) -> str:
    _, marker, remainder = path.read_text(encoding="utf-8").partition(
        "-- +migrate Up"
    )
    if not marker:
        raise ValueError(f"{path.name} has no Up migration")
    return remainder.partition("-- +migrate Down")[0]


@pytest_asyncio.fixture(scope="session")
async def seeded_database():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for control-service integration tests")
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            (TESTS / "upstream_schema.sql").read_text()
        )
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            await connection.execute(migration_up_sql(migration))
        await connection.execute((TESTS / "schema.sql").read_text())
        await connection.execute((TESTS / "seed.sql").read_text())
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def app_client(tmp_path, seeded_database):
    control = tmp_path / "control"
    control.mkdir()
    for name in ("grid.py", "export_leads.py", "source_segments.py"):
        shutil.copy2(SCALE / "control" / name, control / name)
    shutil.copy2(SCALE / "control" / "active_states.yaml", control / "active_states.yaml")
    (control / "triggers").mkdir()
    keywords = tmp_path / "keywords.txt"
    keywords.write_text("plumbers\nelectricians\n")
    exports = tmp_path / "exports"
    exports.mkdir()
    settings = Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        database_url_ro=os.environ["TEST_DATABASE_URL"],
        dash_password="secret",
        control_dir=control,
        keywords_path=keywords,
        active_states_path=control / "active_states.yaml",
        source_segments_path=control / "runtime" / "source_segments.yaml",
        exports_dir=exports,
        enqueue_trigger_path=control / "triggers" / "enqueue.request",
        pipeline_pause_path=control / "runtime" / "pipeline.paused",
        enqueue_trigger_mode="sentinel",
        openrouter_api_key="test-openrouter-key",
        JAWNIX_SCRAPER_CONTROL_TOKEN=(
            "test-scraper-control-token-0000000000000000"
        ),
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={
                "Authorization": (
                    "Bearer test-scraper-control-token-0000000000000000"
                )
            },
        ) as client:
            yield client, settings
