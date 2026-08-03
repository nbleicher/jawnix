"""Add per-Customer allocation controls."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0039"
down_revision = "20260803_0038"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "cooldown_window_days" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("agents")
    }:
        op.add_column(
            "agents",
            sa.Column(
                "cooldown_window_days",
                sa.Integer(),
                nullable=False,
                server_default="7",
            ),
        )
        op.create_check_constraint(
            "ck_agent_cooldown_window_days", "agents", "cooldown_window_days >= 1"
        )
    if "niche_policy_rows" not in _tables():
        op.create_table(
            "niche_policy_rows",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("customer_id", sa.BigInteger(), nullable=False),
            sa.Column("state", sa.String(length=2), nullable=True),
            sa.Column("mode", sa.String(length=8), nullable=False),
            sa.Column("niche", sa.String(length=160), nullable=False),
            sa.CheckConstraint(
                "mode IN ('exclude', 'only')", name="ck_niche_policy_rows_mode"
            ),
            sa.ForeignKeyConstraint(["customer_id"], ["agents.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "customer_id", "state", "mode", "niche", name="uq_niche_policy_row"
            ),
        )
        op.create_index(
            "ix_niche_policy_rows_customer_id", "niche_policy_rows", ["customer_id"]
        )
        op.create_index("ix_niche_policy_rows_state", "niche_policy_rows", ["state"])
    if "niche_assignments" not in _tables():
        op.create_table(
            "niche_assignments",
            sa.Column("phone", sa.String(length=10), primary_key=True),
            sa.Column("niche", sa.String(length=160), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "niche_assignment_uploads" not in _tables():
        op.create_table(
            "niche_assignment_uploads",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("uploaded_by", sa.String(length=160), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("total_rows", sa.Integer(), nullable=False),
            sa.Column("accepted_rows", sa.Integer(), nullable=False),
            sa.Column("invalid_rows", sa.Integer(), nullable=False),
            sa.Column("duplicate_rows", sa.Integer(), nullable=False),
            sa.Column("skipped_mapped_rows", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_niche_assignment_uploads_status",
            "niche_assignment_uploads",
            ["status"],
        )


def downgrade() -> None:
    if "niche_assignment_uploads" in _tables():
        op.drop_index(
            "ix_niche_assignment_uploads_status",
            table_name="niche_assignment_uploads",
        )
        op.drop_table("niche_assignment_uploads")
    if "niche_assignments" in _tables():
        op.drop_table("niche_assignments")
    if "niche_policy_rows" in _tables():
        op.drop_index("ix_niche_policy_rows_state", table_name="niche_policy_rows")
        op.drop_index(
            "ix_niche_policy_rows_customer_id", table_name="niche_policy_rows"
        )
        op.drop_table("niche_policy_rows")
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("agents")
    }
    if "cooldown_window_days" in columns:
        op.drop_constraint(
            "ck_agent_cooldown_window_days", "agents", type_="check"
        )
        op.drop_column("agents", "cooldown_window_days")
