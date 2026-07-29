from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError

from jawnix.database import Base


def load_revision():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260729_0029_scraper_runtime_configuration_revisions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "scraper_runtime_revision_0029",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_configuration_revision_is_append_only(tmp_path):
    database = tmp_path / "runtime-revisions.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    revision = load_revision()
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.upgrade()

    revision_id = str(uuid.uuid4())
    actor_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO scraper_runtime_configuration_revisions (
                    id, before_checksum, after_checksum, configuration,
                    effects, enqueue_requested, actor_user_id, created_at
                ) VALUES (
                    :id, :before_checksum, :after_checksum, :configuration,
                    :effects, :enqueue_requested, :actor_user_id,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": revision_id,
                "before_checksum": "a" * 64,
                "after_checksum": "b" * 64,
                "configuration": "{}",
                "effects": "{}",
                "enqueue_requested": False,
                "actor_user_id": actor_id,
            },
        )

    with pytest.raises(
        DatabaseError,
        match="scraper_runtime_configuration_revisions are immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE scraper_runtime_configuration_revisions
                    SET enqueue_requested = 1
                    WHERE id = :id
                    """
                ),
                {"id": revision_id},
            )
    with pytest.raises(
        DatabaseError,
        match="scraper_runtime_configuration_revisions are immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM scraper_runtime_configuration_revisions
                    WHERE id = :id
                    """
                ),
                {"id": revision_id},
            )

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.downgrade()
    assert (
        "scraper_runtime_configuration_revisions"
        not in inspect(engine).get_table_names()
    )
    engine.dispose()
