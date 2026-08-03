"""Cover shared-history Lead counts with composite indexes.

The Agency directory and details read models count distinct distributed
Leads per permanent history. Composite indexes on (agency_id, lead_id)
and (agent_id, lead_id) let those counts run as index-only scans instead
of heap-fetching millions of distribution_events rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0032"
down_revision = "20260731_0031"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_distribution_events_agency_lead", ["agency_id", "lead_id"]),
    ("ix_distribution_events_agent_lead", ["agent_id", "lead_id"]),
)


def upgrade() -> None:
    existing = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(
            "distribution_events"
        )
    }
    for name, columns in _INDEXES:
        if name in existing:
            continue
        op.create_index(name, "distribution_events", columns)


def downgrade() -> None:
    existing = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(
            "distribution_events"
        )
    }
    for name, _ in _INDEXES:
        if name in existing:
            op.drop_index(name, table_name="distribution_events")
