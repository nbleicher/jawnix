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
        / "20260804_0044_audit_immutability_guards.py"
    )
    spec = importlib.util.spec_from_file_location(
        "audit_immutability_guards_0044",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guarded_engine(tmp_path):
    database = tmp_path / "audit-immutability.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    revision = load_revision()
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.upgrade()
    yield engine, revision
    engine.dispose()


def test_audit_entries_are_immutable(guarded_engine):
    engine, _revision = guarded_engine
    entry_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO audit_entries (
                    id, action, target_type, target_id, actor_user_id,
                    reason, details, created_at
                ) VALUES (
                    :id, 'batch_request_approve', 'batch_request', :id,
                    'admin:1', 'Because', '{}', CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": entry_id},
        )

    with pytest.raises(DatabaseError, match="audit_entries are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE audit_entries SET reason = 'Changed' "
                    "WHERE id = :id"
                ),
                {"id": entry_id},
            )
    with pytest.raises(DatabaseError, match="audit_entries are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM audit_entries WHERE id = :id"),
                {"id": entry_id},
            )


def test_lead_report_resolutions_are_immutable(guarded_engine):
    # SQLite does not enforce foreign keys unless a PRAGMA turns it on, and
    # this engine leaves that off, so the trigger can be exercised directly
    # against a synthetic report_id without building the full Lead Report
    # chain (distribution event, agent, lead) that column belongs to.
    engine, _revision = guarded_engine
    report_id = str(uuid.uuid4())
    resolution_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO lead_report_resolutions (
                    id, report_id, action, note, actor_id, created_at
                ) VALUES (
                    :id, :report_id, 'dismissed', 'Resolved', 'admin:1',
                    CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": resolution_id, "report_id": report_id},
        )

    with pytest.raises(
        DatabaseError,
        match="lead_report_resolutions are immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE lead_report_resolutions SET note = 'Changed' "
                    "WHERE id = :id"
                ),
                {"id": resolution_id},
            )
    with pytest.raises(
        DatabaseError,
        match="lead_report_resolutions are immutable",
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM lead_report_resolutions WHERE id = :id"
                ),
                {"id": resolution_id},
            )


def test_downgrade_removes_triggers(guarded_engine):
    engine, revision = guarded_engine
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.downgrade()

    entry_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO audit_entries (
                    id, action, target_type, target_id, actor_user_id,
                    reason, details, created_at
                ) VALUES (
                    :id, 'batch_request_approve', 'batch_request', :id,
                    'admin:1', 'Because', '{}', CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": entry_id},
        )
        # The trigger is gone, so an update that would have raised before
        # the downgrade now succeeds without complaint.
        connection.execute(
            text(
                "UPDATE audit_entries SET reason = 'Changed' WHERE id = :id"
            ),
            {"id": entry_id},
        )
    assert set(inspect(engine).get_table_names()) >= {
        "audit_entries",
        "lead_report_resolutions",
    }
