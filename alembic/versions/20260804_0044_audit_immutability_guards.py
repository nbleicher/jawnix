"""Refuse UPDATE/DELETE against audit_entries and lead_report_resolutions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260804_0044"
down_revision = "20260804_0043"
branch_labels = None
depends_on = None


#: Both tables are already append-only by convention; these triggers make
#: that a database-level guarantee instead of a hope, matching the pattern
#: established for scraper_runtime_configuration_revisions (#29).
_IMMUTABLE_TABLES = ("audit_entries", "lead_report_resolutions")


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    if bind.dialect.name == "postgresql":
        for table in _IMMUTABLE_TABLES:
            if table not in existing_tables:
                continue
            op.execute(
                sa.text(
                    f"""
                    CREATE OR REPLACE FUNCTION
                        refuse_{table}_change()
                    RETURNS trigger AS $$
                    BEGIN
                        RAISE EXCEPTION '{table} are immutable';
                    END;
                    $$ LANGUAGE plpgsql
                    """
                )
            )
            op.execute(
                sa.text(
                    f"""
                    DROP TRIGGER IF EXISTS {table}_immutable
                    ON {table}
                    """
                )
            )
            op.execute(
                sa.text(
                    f"""
                    CREATE TRIGGER {table}_immutable
                    BEFORE UPDATE OR DELETE
                    ON {table}
                    FOR EACH ROW EXECUTE FUNCTION
                        refuse_{table}_change()
                    """
                )
            )
    else:
        for table in _IMMUTABLE_TABLES:
            if table not in existing_tables:
                continue
            for operation in ("UPDATE", "DELETE"):
                trigger_name = f"{table}_no_{operation.lower()}"
                op.execute(
                    sa.text(
                        f"""
                        CREATE TRIGGER IF NOT EXISTS {trigger_name}
                        BEFORE {operation}
                        ON {table}
                        BEGIN
                            SELECT RAISE(ABORT, '{table} are immutable');
                        END
                        """
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _IMMUTABLE_TABLES:
            op.execute(
                sa.text(
                    f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}"
                )
            )
            op.execute(
                sa.text(f"DROP FUNCTION IF EXISTS refuse_{table}_change()")
            )
    else:
        for table in _IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    sa.text(
                        f"DROP TRIGGER IF EXISTS {table}_no_{operation}"
                    )
                )
