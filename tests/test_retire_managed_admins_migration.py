"""Revision 0055 retires managed administration without losing upload history."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from jawnix.database import Base
from jawnix.models import Agent, LeadUpload, ManagedAdmin

COLUMNS = {"allowed_manager_id", "manager_id"}


def _migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261109_0055_retire_managed_admins.py"
    spec = importlib.util.spec_from_file_location("retire_managed_admins_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _columns(connection) -> dict[str, dict]:
    return {row["name"]: row for row in inspect(connection).get_columns("agents")}


def test_upgrade_drops_assignments_deactivates_managers_and_keeps_uploads(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    Base.metadata.create_all(engine)
    alex, operator = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            # The schema as it stood before this revision.
            migration.downgrade()
        assert COLUMNS <= set(_columns(connection))
        connection.execute(ManagedAdmin.__table__.insert().values(auth_user_id=alex, email="alex@example.test", name="Alex", active=True))
        connection.execute(Agent.__table__.insert().values(
            id=7, slug="harbor", name="Harbor", licensed_states=[], active=True, lead_database="db2",
            billing_enabled=False, cooldown_window_days=7, permanent_history_key="harbor"))
        # The current model no longer maps the assignment columns.
        connection.execute(text("UPDATE agents SET allowed_manager_id = :id, manager_id = :id WHERE id = 7"), {"id": alex.hex})
        assert connection.execute(text("SELECT manager_id FROM agents WHERE id = 7")).scalar() == alex.hex
        connection.execute(LeadUpload.__table__.insert().values(
            id=uuid.uuid4(), manager_id=alex, filename="alex.csv", state="TN", status="complete", path="alex.csv",
            sha256="", bytes=0, processed_rows=0, valid_rows=0, rejected_rows=0, imported_rows=0, updated_rows=0,
            duplicate_rows=0, error=""))
        connection.execute(LeadUpload.__table__.insert().values(
            id=uuid.uuid4(), operator_id=operator, filename="operator.csv", state="KY", status="complete",
            path="operator.csv", sha256="", bytes=0, processed_rows=0, valid_rows=0, rejected_rows=0, imported_rows=0,
            updated_rows=0, duplicate_rows=0, error=""))

        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            # Guarded: a schema built from current models has nothing to drop.
            migration.upgrade()

        assert not COLUMNS & set(_columns(connection))
        assert "ck_customer_manager_allowed" not in {row["name"] for row in inspect(connection).get_check_constraints("agents")}
        assert not [row for row in inspect(connection).get_indexes("agents") if COLUMNS & set(row["column_names"])]
        assert connection.execute(text("SELECT name, lead_database FROM agents WHERE id = 7")).one() == ("Harbor", "db2")
        assert connection.execute(text("SELECT name, active FROM managed_admins")).all() == [("Alex", 0)]
        # Alex's upload still names him; the operator's upload is untouched.
        uploads = dict(connection.execute(text("SELECT filename, manager_id IS NOT NULL FROM lead_uploads")).all())
        assert uploads == {"alex.csv": 1, "operator.csv": 0}

        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.downgrade()
        columns = _columns(connection)
        assert all(columns[name]["nullable"] for name in COLUMNS)
        assert connection.execute(text("SELECT allowed_manager_id, manager_id FROM agents WHERE id = 7")).one() == (None, None)
        assert "ck_customer_manager_allowed" in {row["name"] for row in inspect(connection).get_check_constraints("agents")}
        # Downgrade restores no access: the managers stay inactive.
        assert connection.execute(text("SELECT active FROM managed_admins")).scalar() == 0
    engine.dispose()


def test_a_fresh_schema_only_deactivates(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(ManagedAdmin.__table__.insert().values(auth_user_id=uuid.uuid4(), email="a@example.test", name="A", active=True))
        before = set(_columns(connection))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert set(_columns(connection)) == before
        assert connection.execute(text("SELECT count(*) FROM managed_admins WHERE active")).scalar() == 0
    engine.dispose()
