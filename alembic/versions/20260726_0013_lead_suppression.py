"""Add reversible Lead Suppression materialized state."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0013"
down_revision = "20260726_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("lead_inventory")
    }
    if "suppressed" in columns:
        return
    op.add_column(
        "lead_inventory",
        sa.Column(
            "suppressed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "lead_inventory",
        sa.Column(
            "suppression_reason",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index(
        "ix_lead_inventory_suppressed",
        "lead_inventory",
        ["suppressed"],
    )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("lead_inventory")
    }
    if "suppressed" not in columns:
        return
    op.drop_index(
        "ix_lead_inventory_suppressed",
        table_name="lead_inventory",
    )
    op.drop_column("lead_inventory", "suppression_reason")
    op.drop_column("lead_inventory", "suppressed")
