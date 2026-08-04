"""Allow failed and expired Credit Purchase terminal states."""

from __future__ import annotations

from alembic import op


revision = "20260803_0042"
down_revision = "20260803_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("credit_purchases") as batch:
        batch.drop_constraint(
            "ck_credit_purchase_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_credit_purchase_status",
            "status IN ('processing', 'completed', 'failed', 'expired')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE credit_purchases SET status = 'processing' "
        "WHERE status IN ('failed', 'expired')"
    )
    with op.batch_alter_table("credit_purchases") as batch:
        batch.drop_constraint(
            "ck_credit_purchase_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_credit_purchase_status",
            "status IN ('processing', 'completed')",
        )
