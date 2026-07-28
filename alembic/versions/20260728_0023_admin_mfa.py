"""Add administrator MFA coordination and factor-use state."""

import sqlalchemy as sa
from alembic import op


revision = "20260728_0023"
down_revision = "20260727_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "admin_mfa_states" not in tables:
        op.create_table(
            "admin_mfa_states",
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column(
                "session_generation",
                sa.Integer(),
                nullable=False,
            ),
            sa.Column(
                "enrollment_stage",
                sa.String(length=32),
                nullable=False,
            ),
            sa.Column(
                "enrollment_baseline_factor_ids",
                sa.JSON(),
                nullable=False,
            ),
            sa.Column(
                "enrollment_new_factor_ids",
                sa.JSON(),
                nullable=False,
            ),
            sa.Column("active_factor_id", sa.Uuid(), nullable=True),
            sa.Column(
                "replacement_factor_id",
                sa.Uuid(),
                nullable=True,
            ),
            sa.Column("failed_attempts", sa.Integer(), nullable=False),
            sa.Column(
                "failure_window_started_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "locked_until",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("user_id"),
        )
        op.create_index(
            "ix_admin_mfa_states_enrollment_stage",
            "admin_mfa_states",
            ["enrollment_stage"],
        )
        op.create_index(
            "ix_admin_mfa_states_active_factor_id",
            "admin_mfa_states",
            ["active_factor_id"],
        )
        op.create_index(
            "ix_admin_mfa_states_locked_until",
            "admin_mfa_states",
            ["locked_until"],
        )

    if "admin_mfa_factor_uses" not in tables:
        op.create_table(
            "admin_mfa_factor_uses",
            sa.Column(
                "provider_factor_id",
                sa.Uuid(),
                nullable=False,
            ),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column(
                "last_used_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "ip_address",
                sa.String(length=80),
                nullable=False,
            ),
            sa.Column(
                "user_agent",
                sa.String(length=320),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("provider_factor_id"),
        )
        op.create_index(
            "ix_admin_mfa_factor_uses_user_id",
            "admin_mfa_factor_uses",
            ["user_id"],
        )
        op.create_index(
            "ix_admin_mfa_factor_uses_last_used_at",
            "admin_mfa_factor_uses",
            ["last_used_at"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "admin_mfa_factor_uses" in tables:
        op.drop_index(
            "ix_admin_mfa_factor_uses_last_used_at",
            table_name="admin_mfa_factor_uses",
        )
        op.drop_index(
            "ix_admin_mfa_factor_uses_user_id",
            table_name="admin_mfa_factor_uses",
        )
        op.drop_table("admin_mfa_factor_uses")
    if "admin_mfa_states" in tables:
        for index in (
            "ix_admin_mfa_states_locked_until",
            "ix_admin_mfa_states_active_factor_id",
            "ix_admin_mfa_states_enrollment_stage",
        ):
            op.drop_index(index, table_name="admin_mfa_states")
        op.drop_table("admin_mfa_states")
