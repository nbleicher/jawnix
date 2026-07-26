"""Add scope-bound Inventory Conflict decisions."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0016"
down_revision = "20260726_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "inventory_conflicts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "inventory_conflicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("older_request_id", sa.Uuid(), nullable=False),
        sa.Column("newer_request_id", sa.Uuid(), nullable=False),
        sa.Column("inventory_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "snapshot_checksum",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "decision_by",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "telegram_chat_id",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "telegram_message_id",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["older_request_id"],
            ["lead_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["newer_request_id"],
            ["lead_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "older_request_id",
            "newer_request_id",
            "snapshot_checksum",
            name="uq_inventory_conflict_scope",
        ),
    )
    for column in (
        "older_request_id",
        "newer_request_id",
        "snapshot_checksum",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_inventory_conflicts_{column}",
            "inventory_conflicts",
            [column],
        )


def downgrade() -> None:
    if (
        "inventory_conflicts"
        not in sa.inspect(op.get_bind()).get_table_names()
    ):
        return
    for column in (
        "created_at",
        "status",
        "snapshot_checksum",
        "newer_request_id",
        "older_request_id",
    ):
        op.drop_index(
            f"ix_inventory_conflicts_{column}",
            table_name="inventory_conflicts",
        )
    op.drop_table("inventory_conflicts")
