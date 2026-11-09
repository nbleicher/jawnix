"""Existing Batch Artifacts keep the phone,title layout their checksums record."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261109_0053_batch_artifact_csv_format.py"
    )
    spec = importlib.util.spec_from_file_location("csv_format_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _columns(connection) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns("batch_artifacts")}


def test_upgrade_defaults_existing_artifacts_to_the_original_layout(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE batch_artifacts (id INTEGER PRIMARY KEY, "
                "filename VARCHAR(255) NOT NULL, sha256 VARCHAR(64) NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO batch_artifacts VALUES (1, 'old_batch.zip', :sha)"),
            {"sha": "a" * 64},
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            # Guarded: a schema built from current models already has it.
            migration.upgrade()
        assert connection.execute(
            text("SELECT csv_format, sha256 FROM batch_artifacts")
        ).one() == ("phone_title", "a" * 64)
        # Code that predates the column still writes the original layout.
        connection.execute(
            text("INSERT INTO batch_artifacts (id, filename, sha256) VALUES (2, 'b.zip', 'b')")
        )
        assert connection.execute(
            text("SELECT csv_format FROM batch_artifacts WHERE id = 2")
        ).scalar() == "phone_title"

        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert _columns(connection) == {"id", "filename", "sha256"}
    engine.dispose()


def test_downgrade_refuses_to_drop_the_layout_of_state_column_artifacts(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE batch_artifacts (id INTEGER PRIMARY KEY, "
                "csv_format VARCHAR(32) DEFAULT 'phone_title' NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO batch_artifacts VALUES (1, 'phone_title_state')")
        )
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="state column"):
                migration.downgrade()
        assert "csv_format" in _columns(connection)
    engine.dispose()
