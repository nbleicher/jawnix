"""Add reversible Lead Correction events and active precedence."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0014"
down_revision = "20260726_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "lead_correction_events" not in inspector.get_table_names():
        op.create_table(
            "lead_correction_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("lead_id", sa.BigInteger(), nullable=False),
            sa.Column("action", sa.String(length=24), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("state", sa.String(length=2), nullable=False),
            sa.Column("actor_id", sa.String(length=160), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("supersedes_correction_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["lead_id"],
                ["lead_inventory.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["supersedes_correction_id"],
                ["lead_correction_events.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in (
            "action",
            "created_at",
            "lead_id",
            "supersedes_correction_id",
        ):
            op.create_index(
                f"ix_lead_correction_events_{column}",
                "lead_correction_events",
                [column],
            )
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("lead_inventory")
    }
    if "active_correction_id" not in columns:
        op.add_column(
            "lead_inventory",
            sa.Column("active_correction_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "fk_lead_active_correction",
            "lead_inventory",
            "lead_correction_events",
            ["active_correction_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_lead_inventory_active_correction_id",
            "lead_inventory",
            ["active_correction_id"],
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("lead_inventory")
    }
    if "active_correction_id" in columns:
        op.drop_index(
            "ix_lead_inventory_active_correction_id",
            table_name="lead_inventory",
        )
        op.drop_constraint(
            "fk_lead_active_correction",
            "lead_inventory",
            type_="foreignkey",
        )
        op.drop_column("lead_inventory", "active_correction_id")
    if "lead_correction_events" in sa.inspect(op.get_bind()).get_table_names():
        for column in (
            "supersedes_correction_id",
            "lead_id",
            "created_at",
            "action",
        ):
            op.drop_index(
                f"ix_lead_correction_events_{column}",
                table_name="lead_correction_events",
            )
        op.drop_table("lead_correction_events")
