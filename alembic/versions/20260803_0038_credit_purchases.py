"""Add Credit Purchase rows for Stripe Checkout top-ups."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0038"
down_revision = "20260803_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 20260721_0001 bootstraps empty databases with
    # Base.metadata.create_all(), so these objects may already exist.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("credit_purchases"):
        op.create_table(
            "credit_purchases",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("customer_id", sa.BigInteger(), nullable=False),
            sa.Column("amount_cents", sa.Integer(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="processing",
            ),
            sa.Column(
                "stripe_checkout_session_id",
                sa.String(length=255),
                nullable=False,
            ),
            sa.Column("ledger_entry_id", sa.Uuid(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "amount_cents >= 100 AND amount_cents % 100 = 0",
                name="ck_credit_purchase_whole_dollars",
            ),
            sa.CheckConstraint(
                "status IN ('processing', 'completed')",
                name="ck_credit_purchase_status",
            ),
            sa.ForeignKeyConstraint(
                ["customer_id"],
                ["agents.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["ledger_entry_id"],
                ["credit_ledger_entries.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("stripe_checkout_session_id"),
            sa.UniqueConstraint("ledger_entry_id"),
        )
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("credit_purchases")
    }
    for name, columns in [
        ("ix_credit_purchases_customer_id", ["customer_id"]),
        ("ix_credit_purchases_status", ["status"]),
        ("ix_credit_purchases_created_at", ["created_at"]),
        ("ix_credit_purchase_customer_created", ["customer_id", "created_at"]),
    ]:
        if name not in existing_indexes:
            op.create_index(name, "credit_purchases", columns)


def downgrade() -> None:
    op.drop_index(
        "ix_credit_purchase_customer_created",
        table_name="credit_purchases",
    )
    op.drop_index("ix_credit_purchases_created_at", table_name="credit_purchases")
    op.drop_index("ix_credit_purchases_status", table_name="credit_purchases")
    op.drop_index("ix_credit_purchases_customer_id", table_name="credit_purchases")
    op.drop_table("credit_purchases")
