"""Persist pool analytics snapshots for admin as-of counts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0040"
down_revision = "20260803_0039"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "pool_breakdown_snapshots" not in tables:
        op.create_table(
            "pool_breakdown_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cells", sa.JSON(), nullable=False),
            sa.Column(
                "computed_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
        )
    if "customer_availability_snapshots" not in tables:
        op.create_table(
            "customer_availability_snapshots",
            sa.Column(
                "customer_id",
                sa.BigInteger(),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("available", sa.Integer(), nullable=False),
            sa.Column("forecast", sa.JSON(), nullable=False),
            sa.Column(
                "computed_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_table("customer_availability_snapshots")
    op.drop_table("pool_breakdown_snapshots")
