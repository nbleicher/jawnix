"""Queued-generation migration opens the draft status set and keeps drafts."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def load_revision(name):
    path = Path(__file__).resolve().parents[1] / "alembic/versions" / name
    spec = importlib.util.spec_from_file_location(name, path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def create_pre_queued_drafts_table():
    """The keyword_generation_drafts schema as the 0055 baseline shipped it."""
    op.create_table(
        "keyword_generation_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("administrator_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("seed_keyword", sa.String(length=200), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("exclusion_metrics", sa.JSON(), nullable=False),
        sa.Column("candidate_metrics", sa.JSON(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("acceptance_status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('broad', 'adjacent')",
            name="ck_keyword_generation_drafts_mode",
        ),
        sa.CheckConstraint(
            "acceptance_status IN ('pending', 'accepted')",
            name="ck_keyword_generation_drafts_acceptance_status",
        ),
        sa.CheckConstraint(
            "excluded_count >= 0",
            name="ck_keyword_generation_drafts_excluded_count",
        ),
        sa.CheckConstraint(
            "created_at < expires_at",
            name="ck_keyword_generation_drafts_expiry",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def test_upgrade_allows_queued_statuses_and_is_safe_on_fresh_models(tmp_path):
    queued = load_revision("20261112_0056_queued_keyword_generation.py")
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            create_pre_queued_drafts_table()
        connection.execute(
            text(
                "INSERT INTO keyword_generation_drafts (id, administrator_id, mode, model, terms, exclusion_metrics, candidate_metrics, excluded_count, acceptance_status, created_at, expires_at) "
                "VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'broad', 'test/model', '[]', '{}', '{}', 0, 'pending', '2026-07-28 12:00:00+00:00', '2026-07-29 12:00:00+00:00')"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            queued.upgrade()
            queued.upgrade()
        columns = {
            col["name"] for col in inspect(connection).get_columns("keyword_generation_drafts")
        }
        assert "failure_detail" in columns
        connection.execute(
            text(
                "INSERT INTO keyword_generation_drafts (id, administrator_id, mode, model, terms, exclusion_metrics, candidate_metrics, excluded_count, acceptance_status, created_at, expires_at) "
                "VALUES ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'broad', 'test/model', '[]', '{}', '{}', 0, 'generating', '2026-07-28 12:00:00+00:00', '2026-07-29 12:00:00+00:00')"
            )
        )
        row = connection.execute(
            text(
                "SELECT acceptance_status FROM keyword_generation_drafts WHERE id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'"
            )
        ).one()
        assert row == ("pending",)
        with Operations.context(MigrationContext.configure(connection)):
            queued.downgrade()
        columns = {
            col["name"] for col in inspect(connection).get_columns("keyword_generation_drafts")
        }
        assert "failure_detail" not in columns
        statuses = {
            row[0]
            for row in connection.execute(
                text("SELECT acceptance_status FROM keyword_generation_drafts")
            )
        }
        assert statuses == {"pending"}
    engine.dispose()


def test_upgrade_is_a_no_op_on_a_schema_built_from_current_models(tmp_path):
    from jawnix.database import Base

    queued = load_revision("20261112_0056_queued_keyword_generation.py")
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            queued.upgrade()
    engine.dispose()
