"""Preserve Google Maps observations and current-listing provenance."""

import sqlalchemy as sa
from alembic import op


revision = "20260725_0004"
down_revision = "20260725_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "listing_observations" in inspector.get_table_names():
        return
    op.create_table(
        "listing_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=True),
        sa.Column("dataset_checksum", sa.String(length=64), nullable=False),
        sa.Column("row_number", sa.BigInteger(), nullable=False),
        sa.Column("normalized_phone", sa.String(length=10), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=2), nullable=False),
        sa.Column("source", sa.String(length=160), nullable=False),
        sa.Column("niche", sa.String(length=160), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["lead_inventory.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_checksum",
            "row_number",
            name="uq_listing_observation_dataset_row",
        ),
    )
    op.create_index(
        "ix_listing_observations_dataset_checksum",
        "listing_observations",
        ["dataset_checksum"],
    )
    op.create_index(
        "ix_listing_observations_lead_id",
        "listing_observations",
        ["lead_id"],
    )
    op.create_index(
        "listing_observation_lead_recency_idx",
        "listing_observations",
        ["lead_id", "valid", "observed_at", "row_number"],
    )
    op.add_column(
        "lead_inventory",
        sa.Column(
            "legacy_title",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "lead_inventory",
        sa.Column(
            "legacy_state",
            sa.String(length=2),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "lead_inventory",
        sa.Column(
            "current_listing_observation_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_lead_current_listing_observation",
        "lead_inventory",
        "listing_observations",
        ["current_listing_observation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_lead_inventory_current_listing_observation_id",
        "lead_inventory",
        ["current_listing_observation_id"],
    )
    op.execute(
        sa.text(
            """
            UPDATE lead_inventory
            SET legacy_title = title,
                legacy_state = state
            WHERE current_listing_observation_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lead_inventory_current_listing_observation_id",
        table_name="lead_inventory",
    )
    inspector = sa.inspect(op.get_bind())
    current_listing_fk = next(
        (
            foreign_key["name"]
            for foreign_key in inspector.get_foreign_keys("lead_inventory")
            if foreign_key["constrained_columns"]
            == ["current_listing_observation_id"]
        ),
        None,
    )
    if current_listing_fk:
        op.drop_constraint(
            current_listing_fk,
            "lead_inventory",
            type_="foreignkey",
        )
    op.drop_column("lead_inventory", "current_listing_observation_id")
    op.drop_column("lead_inventory", "legacy_state")
    op.drop_column("lead_inventory", "legacy_title")
    op.drop_index(
        "listing_observation_lead_recency_idx",
        table_name="listing_observations",
    )
    op.drop_index(
        "ix_listing_observations_lead_id",
        table_name="listing_observations",
    )
    op.drop_index(
        "ix_listing_observations_dataset_checksum",
        table_name="listing_observations",
    )
    op.drop_table("listing_observations")
