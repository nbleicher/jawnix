"""Add anonymous Customer Tombstones for personal-data erasure."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0019"
down_revision = "20260726_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "customer_tombstones" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "customer_tombstones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("former_customer_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "opaque_key",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "erased_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("former_customer_id"),
        sa.UniqueConstraint("opaque_key"),
    )


def downgrade() -> None:
    if "customer_tombstones" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("customer_tombstones")
