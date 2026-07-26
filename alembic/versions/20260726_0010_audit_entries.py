"""Add immutable privileged action audit entries."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0010"
down_revision = "20260726_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "audit_entries" in inspector.get_table_names():
        return
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=160), nullable=False),
        sa.Column("actor_user_id", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "action",
        "target_type",
        "target_id",
        "actor_user_id",
        "created_at",
    ):
        op.create_index(
            f"ix_audit_entries_{column}",
            "audit_entries",
            [column],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "audit_entries" not in inspector.get_table_names():
        return
    for column in (
        "created_at",
        "actor_user_id",
        "target_id",
        "target_type",
        "action",
    ):
        op.drop_index(
            f"ix_audit_entries_{column}",
            table_name="audit_entries",
        )
    op.drop_table("audit_entries")
