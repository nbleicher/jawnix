import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_upgrade_retains_reserve_and_is_idempotent(tmp_path):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261014_0048_keyword_focus_spend.py"
    )
    spec = importlib.util.spec_from_file_location("focus_spend_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as db:
        db.execute(
            text(
                "CREATE TABLE keyword_candidate_pool (id INTEGER PRIMARY KEY,candidates JSON NOT NULL,next_refill_at TIMESTAMP)"
            )
        )
        db.execute(
            text(
                "INSERT INTO keyword_candidate_pool VALUES (1,'[{\"term\":\"roof repair\"}]','2026-10-14 04:00:00')"
            )
        )
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
            migration.upgrade()
        row = db.execute(
            text(
                "SELECT candidates,next_refill_at,focus_cooldowns FROM keyword_candidate_pool"
            )
        ).one()
        assert row == ('[{"term":"roof repair"}]', "2026-10-14 04:00:00", "{}")
        assert inspect(db).has_table("keyword_generation_charges")
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
        assert not inspect(db).has_table("keyword_generation_charges")
    engine.dispose()
