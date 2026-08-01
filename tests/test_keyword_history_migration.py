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
        / "20260731_0030_keyword_history.py"
    )
    spec = importlib.util.spec_from_file_location(
        "keyword_history_revision_0030",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keyword_history_migration_owns_records_and_import_proof(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'keyword-history.db'}")
    revision = load_revision()
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.upgrade()

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "keyword_history",
        "keyword_history_imports",
    }
    assert {column["name"] for column in inspector.get_columns("keyword_history")} == {
        "id",
        "term",
        "origin",
        "first_seen_at",
        "last_seen_at",
    }
    assert {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("keyword_history")
    } == {("term", "origin")}

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.downgrade()
    assert inspect(engine).get_table_names() == []
    engine.dispose()
