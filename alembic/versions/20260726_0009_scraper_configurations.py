"""Add immutable Scraper Configurations and Source Segments."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0009"
down_revision = "20260726_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "scraper_configurations" in inspector.get_table_names():
        return
    op.create_table(
        "scraper_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("anomaly_thresholds", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("based_on_configuration_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["based_on_configuration_id"],
            ["scraper_configurations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    op.create_index(
        "ix_scraper_configurations_based_on_configuration_id",
        "scraper_configurations",
        ["based_on_configuration_id"],
    )
    op.create_index(
        "ix_scraper_configurations_checksum",
        "scraper_configurations",
        ["checksum"],
    )
    op.create_index(
        "ix_scraper_configurations_created_at",
        "scraper_configurations",
        ["created_at"],
    )
    op.create_index(
        "ix_scraper_configurations_status",
        "scraper_configurations",
        ["status"],
    )
    op.create_table(
        "source_segments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("configuration_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("niche", sa.String(length=160), nullable=False),
        sa.Column("query", sa.String(length=320), nullable=False),
        sa.Column("geography", sa.String(length=320), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["scraper_configurations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "configuration_id",
            "key",
            name="uq_source_segment_configuration_key",
        ),
    )
    op.create_index(
        "ix_source_segments_configuration_id",
        "source_segments",
        ["configuration_id"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "source_segments" in inspector.get_table_names():
        op.drop_index(
            "ix_source_segments_configuration_id",
            table_name="source_segments",
        )
        op.drop_table("source_segments")
    if "scraper_configurations" in inspector.get_table_names():
        op.drop_index(
            "ix_scraper_configurations_status",
            table_name="scraper_configurations",
        )
        op.drop_index(
            "ix_scraper_configurations_checksum",
            table_name="scraper_configurations",
        )
        op.drop_index(
            "ix_scraper_configurations_created_at",
            table_name="scraper_configurations",
        )
        op.drop_index(
            "ix_scraper_configurations_based_on_configuration_id",
            table_name="scraper_configurations",
        )
        op.drop_table("scraper_configurations")
