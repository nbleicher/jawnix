"""Revision 0055 on real PostgreSQL: both legacy constraint namings, and the CLI."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import sql
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from jawnix.database import Base
from jawnix.job_wakeup import postgres_dsn

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")
ROOT = Path(__file__).resolve().parents[1]
COLUMNS = {"allowed_manager_id", "manager_id"}


@pytest.fixture
def database_url():
    base = make_url(DATABASE_URL)
    assert base.database.endswith("_test"), "Use a disposable *_test database"
    name = f"jawnix_{uuid.uuid4().hex[:16]}_test"
    with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(postgres_dsn(DATABASE_URL), autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def _migration():
    path = ROOT / "alembic/versions/20261109_0055_retire_managed_admins.py"
    spec = importlib.util.spec_from_file_location("retire_managed_admins_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _constraints(connection) -> set[str]:
    return set(connection.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'agents'::regclass"
    )).scalars())


def _seed(connection, alex: uuid.UUID) -> None:
    connection.execute(text(
        "INSERT INTO managed_admins (auth_user_id, email, name, active, created_at) "
        "VALUES (:id, 'alex@example.test', 'Alex', true, now())"), {"id": alex})
    connection.execute(text(
        "INSERT INTO agents (slug, name, licensed_states, active, lead_database, allowed_manager_id, manager_id, "
        "billing_enabled, cooldown_window_days, permanent_history_key) "
        "VALUES ('harbor', 'Harbor', '[]', true, 'db2', :id, :id, false, 7, 'harbor')"), {"id": alex})
    connection.execute(text(
        "INSERT INTO lead_uploads (id, manager_id, filename, state, status, path, sha256, bytes, processed_rows, "
        "valid_rows, rejected_rows, imported_rows, updated_rows, duplicate_rows, error, created_at) "
        "VALUES (:upload, :id, 'alex.csv', 'TN', 'complete', 'alex.csv', '', 0, 0, 0, 0, 0, 0, 0, '', now())"),
        {"upload": uuid.uuid4(), "id": alex})


def _assert_retired(connection) -> None:
    assert not COLUMNS & {row["name"] for row in inspect(connection).get_columns("agents")}
    assert not {name for name in _constraints(connection) if "manager" in name}
    assert not [row for row in inspect(connection).get_indexes("agents") if "manager" in row["name"]]
    assert connection.execute(text("SELECT name, lead_database FROM agents")).one() == ("Harbor", "db2")
    assert connection.execute(text("SELECT bool_or(active) FROM managed_admins")).scalar() is False
    assert connection.execute(text(
        "SELECT m.name FROM lead_uploads u JOIN managed_admins m ON m.auth_user_id = u.manager_id"
    )).scalar() == "Alex"


def test_revision_0046_names_upgrade_and_downgrade(database_url):
    migration = _migration()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert {"fk_customer_allowed_manager", "fk_customer_manager", "ck_customer_manager_allowed"} <= _constraints(connection)
        _seed(connection, uuid.uuid4())
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        _assert_retired(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        columns = {row["name"]: row for row in inspect(connection).get_columns("agents")}
        assert all(columns[name]["nullable"] for name in COLUMNS)
        assert connection.execute(text("SELECT allowed_manager_id, manager_id FROM agents")).one() == (None, None)
        assert {"fk_customer_allowed_manager", "fk_customer_manager", "ck_customer_manager_allowed"} <= _constraints(connection)
        assert {"ix_agents_allowed_manager_id", "ix_agents_manager_id"} <= {row["name"] for row in inspect(connection).get_indexes("agents")}
    engine.dispose()


def test_default_constraint_names_from_a_model_built_schema(database_url):
    # A schema bootstrapped from the models of revisions 0046 to 0054 has the
    # database's own foreign key names instead of revision 0046's.
    migration = _migration()
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE agents "
            "ADD COLUMN allowed_manager_id uuid REFERENCES managed_admins (auth_user_id) ON DELETE RESTRICT, "
            "ADD COLUMN manager_id uuid REFERENCES managed_admins (auth_user_id) ON DELETE RESTRICT, "
            "ADD CONSTRAINT ck_customer_manager_allowed CHECK (manager_id IS NULL OR "
            "(allowed_manager_id IS NOT NULL AND manager_id = allowed_manager_id))"))
        connection.execute(text("CREATE INDEX ix_agents_allowed_manager_id ON agents (allowed_manager_id)"))
        connection.execute(text("CREATE INDEX ix_agents_manager_id ON agents (manager_id)"))
        assert {"agents_allowed_manager_id_fkey", "agents_manager_id_fkey"} <= _constraints(connection)
        _seed(connection, uuid.uuid4())
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        _assert_retired(connection)
    engine.dispose()


def test_alembic_cli_upgrades_downgrades_and_upgrades_again(database_url):
    env = {**os.environ, "DATABASE_URL": database_url,
           "JAWNIX_SESSION_SECRET": os.environ.get("JAWNIX_SESSION_SECRET", "bootstrap-regression-secret-long-enough")}

    def alembic(*args: str) -> None:
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr

    engine = create_engine(database_url)

    def state() -> tuple[str, set[str]]:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            return version, COLUMNS & {row["name"] for row in inspect(connection).get_columns("agents")}

    alembic("upgrade", "head")
    assert state() == ("20261109_0055", set())
    alembic("downgrade", "20261109_0054")
    assert state() == ("20261109_0054", COLUMNS)
    alembic("upgrade", "head")
    assert state() == ("20261109_0055", set())
    engine.dispose()
