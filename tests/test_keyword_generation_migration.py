from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def load_revision():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260731_0031_keyword_generation_drafts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "keyword_generation_revision_0031",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keyword_generation_migration_persists_draft_provenance_and_metrics(
    tmp_path,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'generation.db'}")
    revision = load_revision()
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.upgrade()

    inspector = inspect(engine)
    assert inspector.get_table_names() == ["keyword_generation_drafts"]
    assert {
        column["name"]
        for column in inspector.get_columns("keyword_generation_drafts")
    } == {
        "id",
        "administrator_id",
        "mode",
        "seed_keyword",
        "model",
        "terms",
        "exclusion_metrics",
        "candidate_metrics",
        "excluded_count",
        "acceptance_status",
        "created_at",
        "expires_at",
        "accepted_at",
    }

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.downgrade()
    assert inspect(engine).get_table_names() == []
    engine.dispose()
