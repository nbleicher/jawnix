"""Persist per-focus refill cooldowns and native keyword spend reservations."""

import sqlalchemy as sa

from alembic import op

revision = "20261014_0048"
down_revision = "20261013_0047"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {
        row["name"] for row in sa.inspect(bind).get_columns("keyword_candidate_pool")
    }
    if "focus_cooldowns" not in columns:
        op.add_column(
            "keyword_candidate_pool",
            sa.Column(
                "focus_cooldowns", sa.JSON(), nullable=False, server_default="{}"
            ),
        )
    if not sa.inspect(bind).has_table("keyword_generation_charges"):
        op.create_table(
            "keyword_generation_charges",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("budget_day", sa.Date(), nullable=False),
            sa.Column("model", sa.String(80), nullable=False),
            sa.Column("purpose", sa.String(80), nullable=False),
            sa.Column("reserved_usd", sa.Numeric(14, 10), nullable=False),
            sa.Column("metered_usd", sa.Numeric(14, 10)),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "reserved_usd >= 0", name="ck_keyword_charge_reservation"
            ),
            sa.CheckConstraint(
                "metered_usd IS NULL OR metered_usd >= 0",
                name="ck_keyword_charge_metered",
            ),
            sa.CheckConstraint(
                "status IN ('pending', 'settled', 'uncertain')",
                name="ck_keyword_charge_status",
            ),
        )
        op.create_index(
            "ix_keyword_generation_charges_budget_day",
            "keyword_generation_charges",
            ["budget_day"],
        )


def downgrade():
    op.drop_table("keyword_generation_charges")
    op.drop_column("keyword_candidate_pool", "focus_cooldowns")
