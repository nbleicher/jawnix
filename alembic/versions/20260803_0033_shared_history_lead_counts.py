"""Persist shared-history distributed-Lead counts.

Recomputing the count behind an Agency card walks millions of
distribution_events rows. The read models serve this persisted row and
refresh it in the background once stale.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0033"
down_revision = "20260803_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "shared_history_lead_counts" in tables:
        return
    op.create_table(
        "shared_history_lead_counts",
        sa.Column("subject_key", sa.Text(), nullable=False),
        sa.Column("lead_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("subject_key"),
    )


def downgrade() -> None:
    op.drop_table("shared_history_lead_counts")
