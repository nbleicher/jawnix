"""Persist Agency-first fulfillment rotation state."""

import sqlalchemy as sa
from alembic import op


revision = "20260725_0005"
down_revision = "20260725_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {
        column["name"] for column in inspector.get_columns("agencies")
    }
    if "last_fulfilled_at" in existing_columns:
        return
    op.add_column(
        "agencies",
        sa.Column("last_fulfilled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column("last_fulfilled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agencies_last_fulfilled_at",
        "agencies",
        ["last_fulfilled_at"],
    )
    op.create_index(
        "ix_agents_last_fulfilled_at",
        "agents",
        ["last_fulfilled_at"],
    )
    op.execute(
        sa.text(
            """
            UPDATE agents
            SET last_fulfilled_at = (
                SELECT MAX(distribution_events.delivered_at)
                FROM distribution_events
                WHERE distribution_events.agent_id = agents.id
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agencies
            SET last_fulfilled_at = (
                SELECT MAX(distribution_events.delivered_at)
                FROM distribution_events
                WHERE distribution_events.agency_id = agencies.id
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_agents_last_fulfilled_at", table_name="agents")
    op.drop_index("ix_agencies_last_fulfilled_at", table_name="agencies")
    op.drop_column("agents", "last_fulfilled_at")
    op.drop_column("agencies", "last_fulfilled_at")
