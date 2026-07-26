"""Add per-segment acquisition evidence and anomaly holds."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0012"
down_revision = "20260726_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "scrape_segment_results" not in inspector.get_table_names():
        op.create_table(
            "scrape_segment_results",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("scraper_run_id", sa.BigInteger(), nullable=False),
            sa.Column("segment_key", sa.String(length=160), nullable=False),
            sa.Column("niche", sa.String(length=160), nullable=False),
            sa.Column("geography", sa.String(length=320), nullable=False),
            sa.Column("observed_count", sa.BigInteger(), nullable=False),
            sa.Column("valid_count", sa.BigInteger(), nullable=False),
            sa.Column("new_count", sa.BigInteger(), nullable=False),
            sa.Column("duplicate_count", sa.BigInteger(), nullable=False),
            sa.Column("quarantined_count", sa.BigInteger(), nullable=False),
            sa.Column("anomalous", sa.Boolean(), nullable=False),
            sa.Column("anomaly_reasons", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(
                ["scraper_run_id"],
                ["scraper_runs.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scraper_run_id",
                "segment_key",
                name="uq_scrape_run_segment_result",
            ),
        )
        for column in ("anomalous", "niche", "scraper_run_id", "segment_key"):
            op.create_index(
                f"ix_scrape_segment_results_{column}",
                "scrape_segment_results",
                [column],
            )
    inspector = sa.inspect(op.get_bind())
    if "scrape_anomalies" in inspector.get_table_names():
        return
    op.create_table(
        "scrape_anomalies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scraper_run_id", sa.BigInteger(), nullable=False),
        sa.Column("configuration_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("decision_by", sa.String(length=160), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_chat_id", sa.String(length=120), nullable=False),
        sa.Column("telegram_message_id", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["scraper_configurations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scraper_run_id"],
            ["scraper_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scraper_run_id"),
    )
    for column in (
        "configuration_id",
        "dataset_checksum",
        "scraper_run_id",
        "status",
    ):
        op.create_index(
            f"ix_scrape_anomalies_{column}",
            "scrape_anomalies",
            [column],
            unique=column == "scraper_run_id",
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "scrape_anomalies" in inspector.get_table_names():
        for column in (
            "status",
            "scraper_run_id",
            "dataset_checksum",
            "configuration_id",
        ):
            op.drop_index(
                f"ix_scrape_anomalies_{column}",
                table_name="scrape_anomalies",
            )
        op.drop_table("scrape_anomalies")
    if "scrape_segment_results" in inspector.get_table_names():
        for column in (
            "segment_key",
            "scraper_run_id",
            "niche",
            "anomalous",
        ):
            op.drop_index(
                f"ix_scrape_segment_results_{column}",
                table_name="scrape_segment_results",
            )
        op.drop_table("scrape_segment_results")
