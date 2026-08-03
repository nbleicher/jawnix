"""Serve per-Customer delivered bounds as index probes.

The Customer details read model asks for min/max delivered_at per
Customer; without (agent_id, delivered_at) that heap-fetches every one
of the Customer's distribution_events rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0034"
down_revision = "20260803_0033"
branch_labels = None
depends_on = None

_NAME = "ix_distribution_events_agent_delivered"


def upgrade() -> None:
    existing = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(
            "distribution_events"
        )
    }
    if _NAME in existing:
        return
    op.create_index(
        _NAME, "distribution_events", ["agent_id", "delivered_at"]
    )


def downgrade() -> None:
    existing = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(
            "distribution_events"
        )
    }
    if _NAME in existing:
        op.drop_index(_NAME, table_name="distribution_events")
