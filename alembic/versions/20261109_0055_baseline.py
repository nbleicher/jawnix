"""Baseline: the full current schema, squashed from revisions 0001-0055.

Builds every table, index, constraint, and the batch-number sequence from
the current models, then installs the append-only triggers the squashed
revisions (0028, 0029, 0036, 0044) added outside SQLAlchemy metadata.

The revision ID keeps the former chain head (20261109_0055) so a database
already stamped at head is up to date with no migration to run. A database
stamped at an older revision must be upgraded to 20261109_0055 with the
pre-squash chain first (see docs/OPERATIONS.md, "Alembic baseline").
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from jawnix.database import Base
from jawnix import models  # noqa: F401


revision = "20261109_0055"
down_revision = None
branch_labels = None
depends_on = None


#: Tables the database itself keeps append-only: (table, PostgreSQL trigger
#: function, PostgreSQL trigger, SQLite trigger prefix, error message).
#: Names match the squashed revisions exactly so a squashed schema and a
#: chain-built schema are indistinguishable.
_APPEND_ONLY_GUARDS = (
    (
        "user_account_migration_artifacts",
        "refuse_user_account_migration_artifact_change",
        "user_account_migration_artifacts_immutable",
        "user_account_migration_artifacts_no_",
        "user_account_migration_artifacts are immutable",
    ),
    (
        "scraper_runtime_configuration_revisions",
        "refuse_scraper_runtime_configuration_revision_change",
        "scraper_runtime_configuration_revisions_immutable",
        "scraper_runtime_configuration_revisions_no_",
        "scraper_runtime_configuration_revisions are immutable",
    ),
    (
        "credit_ledger_entries",
        "refuse_credit_ledger_mutation",
        "credit_ledger_append_only",
        "credit_ledger_append_only_",
        "Credit Ledger entries are append-only",
    ),
    (
        "audit_entries",
        "refuse_audit_entries_change",
        "audit_entries_immutable",
        "audit_entries_no_",
        "audit_entries are immutable",
    ),
    (
        "lead_report_resolutions",
        "refuse_lead_report_resolutions_change",
        "lead_report_resolutions_immutable",
        "lead_report_resolutions_no_",
        "lead_report_resolutions are immutable",
    ),
)


def _install_append_only_guards() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table, function, trigger, _prefix, message in _APPEND_ONLY_GUARDS:
            op.execute(
                sa.text(
                    f"""
                    CREATE OR REPLACE FUNCTION {function}()
                    RETURNS trigger AS $$
                    BEGIN
                        RAISE EXCEPTION '{message}';
                    END;
                    $$ LANGUAGE plpgsql
                    """
                )
            )
            op.execute(
                sa.text(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
            )
            op.execute(
                sa.text(
                    f"""
                    CREATE TRIGGER {trigger}
                    BEFORE UPDATE OR DELETE ON {table}
                    FOR EACH ROW EXECUTE FUNCTION {function}()
                    """
                )
            )
    else:
        for table, _function, _trigger, prefix, message in _APPEND_ONLY_GUARDS:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    sa.text(
                        f"""
                        CREATE TRIGGER IF NOT EXISTS
                            {prefix}{operation.lower()}
                        BEFORE {operation} ON {table}
                        BEGIN
                            SELECT RAISE(ABORT, '{message}');
                        END
                        """
                    )
                )


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())
    _install_append_only_guards()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table, function, trigger, _prefix, _message in _APPEND_ONLY_GUARDS:
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger} ON {table}"))
            op.execute(sa.text(f"DROP FUNCTION IF EXISTS {function}()"))
    else:
        for table, _function, _trigger, prefix, _message in _APPEND_ONLY_GUARDS:
            for operation in ("update", "delete"):
                op.execute(sa.text(f"DROP TRIGGER IF EXISTS {prefix}{operation}"))
    Base.metadata.drop_all(bind=bind)
