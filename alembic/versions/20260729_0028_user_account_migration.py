"""Journal and reconcile the one-time User Account migration."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260729_0028"
down_revision = "20260729_0027"
branch_labels = None
depends_on = None

_ID_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user_account_migration_runs" not in inspector.get_table_names():
        op.create_table(
            "user_account_migration_runs",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("input_checksum", sa.String(length=64), nullable=False),
            sa.Column("plan_checksum", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("operator", sa.String(length=160), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column(
                "backup_receipt_checksum",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column("backup_snapshot", sa.String(length=160), nullable=False),
            sa.Column("backup_receipts", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "completed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.CheckConstraint(
                "status IN ('in_progress', 'completed')",
                name="ck_user_account_migration_runs_status",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "input_checksum",
                name="uq_user_account_migration_runs_input_checksum",
            ),
        )

    inspector = sa.inspect(op.get_bind())
    if "user_account_migration_mappings" not in inspector.get_table_names():
        op.create_table(
            "user_account_migration_mappings",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("customer_id", _ID_TYPE, nullable=False),
            sa.Column("customer_slug", sa.String(length=80), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("agency_id", _ID_TYPE, nullable=True),
            sa.Column("agency_slug", sa.String(length=80), nullable=False),
            sa.Column("prior_auth_user_id", sa.Uuid(), nullable=True),
            sa.Column("invited_auth_user_id", sa.Uuid(), nullable=True),
            sa.Column("invitation_id", sa.Uuid(), nullable=True),
            sa.Column("state", sa.String(length=32), nullable=False),
            sa.Column(
                "deactivation_state",
                sa.String(length=40),
                nullable=False,
            ),
            sa.Column("agency_before_id", _ID_TYPE, nullable=True),
            sa.Column("agency_result", sa.JSON(), nullable=False),
            sa.Column("history_counts", sa.JSON(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.CheckConstraint(
                (
                    "state IN ('planned', 'dispatching', 'failed', "
                    "'invited_pending', 'active')"
                ),
                name="ck_user_account_migration_mappings_state",
            ),
            sa.ForeignKeyConstraint(
                ["agency_id"],
                ["agencies.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["customer_id"],
                ["agents.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["invitation_id"],
                ["user_account_invitations.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["user_account_migration_runs.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id",
                "row_number",
                name="uq_user_account_migration_run_row",
            ),
        )
        op.create_index(
            "ix_user_account_migration_mappings_run_id",
            "user_account_migration_mappings",
            ["run_id"],
        )
        op.create_index(
            "ix_user_account_migration_mappings_customer_id",
            "user_account_migration_mappings",
            ["customer_id"],
        )
        op.create_index(
            "ix_user_account_migration_mappings_agency_id",
            "user_account_migration_mappings",
            ["agency_id"],
        )
        op.create_index(
            "ix_user_account_migration_mappings_state",
            "user_account_migration_mappings",
            ["state"],
        )

    inspector = sa.inspect(op.get_bind())
    if "user_account_migration_artifacts" not in inspector.get_table_names():
        op.create_table(
            "user_account_migration_artifacts",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("run_id", sa.Uuid(), nullable=False),
            sa.Column("checksum", sa.String(length=64), nullable=False),
            sa.Column("contents", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["user_account_migration_runs.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "checksum",
                name="uq_user_account_migration_artifacts_checksum",
            ),
            sa.UniqueConstraint(
                "run_id",
                name="uq_user_account_migration_artifacts_run_id",
            ),
        )
    # Revision 0001 builds current metadata on a fresh database, so the table
    # may pre-exist by the time 0028 runs. Enforce immutability independently
    # of table creation so fresh installs and real upgrades converge.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION
                    refuse_user_account_migration_artifact_change()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION
                        'user_account_migration_artifacts are immutable';
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        op.execute(
            sa.text(
                """
                DROP TRIGGER IF EXISTS
                    user_account_migration_artifacts_immutable
                ON user_account_migration_artifacts
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER user_account_migration_artifacts_immutable
                BEFORE UPDATE OR DELETE
                ON user_account_migration_artifacts
                FOR EACH ROW EXECUTE FUNCTION
                refuse_user_account_migration_artifact_change()
                """
            )
        )
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                sa.text(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS
                        user_account_migration_artifacts_no_{operation.lower()}
                    BEFORE {operation}
                    ON user_account_migration_artifacts
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'user_account_migration_artifacts are immutable'
                        );
                    END
                    """
                )
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user_account_migration_artifacts" in inspector.get_table_names():
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                sa.text(
                    """
                    DROP TRIGGER IF EXISTS
                        user_account_migration_artifacts_immutable
                    ON user_account_migration_artifacts
                    """
                )
            )
            op.execute(
                sa.text(
                    """
                    DROP FUNCTION IF EXISTS
                        refuse_user_account_migration_artifact_change()
                    """
                )
            )
        else:
            for operation in ("update", "delete"):
                op.execute(
                    sa.text(
                        "DROP TRIGGER IF EXISTS "
                        f"user_account_migration_artifacts_no_{operation}"
                    )
                )
        op.drop_table("user_account_migration_artifacts")
    if "user_account_migration_mappings" in inspector.get_table_names():
        for index in (
            "ix_user_account_migration_mappings_state",
            "ix_user_account_migration_mappings_agency_id",
            "ix_user_account_migration_mappings_customer_id",
            "ix_user_account_migration_mappings_run_id",
        ):
            op.drop_index(
                index,
                table_name="user_account_migration_mappings",
            )
        op.drop_table("user_account_migration_mappings")
    if "user_account_migration_runs" in inspector.get_table_names():
        op.drop_table("user_account_migration_runs")
