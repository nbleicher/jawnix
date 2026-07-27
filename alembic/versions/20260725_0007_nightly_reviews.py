"""Persist one operational Nightly Review per scraper run."""

import sqlalchemy as sa
from alembic import op


revision = "20260725_0007"
down_revision = "20260725_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "nightly_reviews" in inspector.get_table_names():
        return
    op.create_table(
        "nightly_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scraper_run_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column(
            "telegram_message_id",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scraper_run_id"],
            ["scraper_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scraper_run_id"),
    )
    op.create_index(
        "ix_nightly_reviews_created_at",
        "nightly_reviews",
        ["created_at"],
    )
    op.create_index(
        "ix_nightly_reviews_scraper_run_id",
        "nightly_reviews",
        ["scraper_run_id"],
        unique=True,
    )
    op.create_index(
        "ix_nightly_reviews_status",
        "nightly_reviews",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nightly_reviews_status",
        table_name="nightly_reviews",
    )
    op.drop_index(
        "ix_nightly_reviews_scraper_run_id",
        table_name="nightly_reviews",
    )
    op.drop_index(
        "ix_nightly_reviews_created_at",
        table_name="nightly_reviews",
    )
    op.drop_table("nightly_reviews")
