"""Track crash-safe Nightly Review Telegram delivery."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0020"
down_revision = "20260726_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(
            "nightly_reviews"
        )
    }
    if "telegram_delivery_state" not in columns:
        op.add_column(
            "nightly_reviews",
            sa.Column(
                "telegram_delivery_state",
                sa.String(length=24),
                server_default="pending",
                nullable=False,
            ),
        )
        op.create_index(
            "ix_nightly_reviews_telegram_delivery_state",
            "nightly_reviews",
            ["telegram_delivery_state"],
        )
    if "telegram_delivery_started_at" not in columns:
        op.add_column(
            "nightly_reviews",
            sa.Column(
                "telegram_delivery_started_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
    if "telegram_delivery_error" not in columns:
        op.add_column(
            "nightly_reviews",
            sa.Column(
                "telegram_delivery_error",
                sa.Text(),
                server_default="",
                nullable=False,
            ),
        )
    op.execute(
        sa.text(
            """
            UPDATE nightly_reviews
            SET telegram_delivery_state = 'sent'
            WHERE telegram_message_id <> ''
              AND telegram_delivery_state = 'pending'
            """
        )
    )


def downgrade() -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(
            "nightly_reviews"
        )
    }
    if "telegram_delivery_error" in columns:
        op.drop_column(
            "nightly_reviews",
            "telegram_delivery_error",
        )
    if "telegram_delivery_started_at" in columns:
        op.drop_column(
            "nightly_reviews",
            "telegram_delivery_started_at",
        )
    if "telegram_delivery_state" in columns:
        op.drop_index(
            "ix_nightly_reviews_telegram_delivery_state",
            table_name="nightly_reviews",
        )
        op.drop_column(
            "nightly_reviews",
            "telegram_delivery_state",
        )
