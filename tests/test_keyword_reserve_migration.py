"""Reserve migration preserves prepared work and provider failure backoff."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_upgrade_retains_existing_candidates_and_is_safe_on_fresh_models(tmp_path):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261013_0047_keyword_reserve_refill.py"
    )
    spec = importlib.util.spec_from_file_location("reserve_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE keyword_candidate_pool (id INTEGER PRIMARY KEY, candidates JSON NOT NULL, next_refill_at TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO keyword_candidate_pool VALUES (1, '[{\"term\":\"roof repair\"}]', '2026-10-13 20:15:00')"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        row = connection.execute(
            text(
                "SELECT candidates,next_refill_at,last_refill_at,refilling FROM keyword_candidate_pool"
            )
        ).one()
        assert row == ('[{"term":"roof repair"}]', "2026-10-13 20:15:00", None, 0)
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert {
            col["name"]
            for col in inspect(connection).get_columns("keyword_candidate_pool")
        } == {"id", "candidates", "next_refill_at"}
    engine.dispose()
