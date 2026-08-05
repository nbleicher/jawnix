"""The Alembic chain must bootstrap an empty PostgreSQL database (#181).

Revision 20260721_0001 creates the schema from the current models via
Base.metadata.create_all(), so later revisions only apply cleanly when they
guard every metadata object they create. This runs the real chain against a
scratch database created inside the acceptance PostgreSQL instance.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    not os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL"),
    reason="requires the real PostgreSQL acceptance database",
)
def test_alembic_chain_bootstraps_empty_postgresql():
    admin_url = os.environ["JAWNIX_ACCEPTANCE_DATABASE_URL"]
    scratch = f"jawnix_bootstrap_{uuid.uuid4().hex[:12]}"
    admin = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{scratch}"'))
    repo_root = Path(__file__).resolve().parent.parent
    scratch_url = admin_url.rsplit("/", 1)[0] + "/" + scratch
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=repo_root,
            env={
                **os.environ,
                "DATABASE_URL": scratch_url,
                "JAWNIX_SESSION_SECRET": os.environ.get(
                    "JAWNIX_SESSION_SECRET",
                    "bootstrap-regression-secret-long-enough",
                ),
            },
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, result.stderr

        expected_head = ScriptDirectory.from_config(
            Config(str(repo_root / "alembic.ini"))
        ).get_current_head()
        engine = create_engine(scratch_url)
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
            billing_constraints = connection.execute(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conname = 'ck_agent_billing_rate_required'"
                )
            ).scalar()
            ledger_triggers = connection.execute(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname = 'credit_ledger_append_only'"
                )
            ).scalar()
        engine.dispose()

        assert version == expected_head
        assert billing_constraints == 1
        assert ledger_triggers == 1
    finally:
        with admin.connect() as connection:
            connection.execute(
                text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
            )
        admin.dispose()
