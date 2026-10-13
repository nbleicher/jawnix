"""Persist reserve refill hysteresis and the last successful refill time."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_0047"
down_revision = "20261013_0046"
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        row["name"]
        for row in sa.inspect(op.get_bind()).get_columns("keyword_candidate_pool")
    }
    if "last_refill_at" not in columns:
        op.add_column(
            "keyword_candidate_pool",
            sa.Column("last_refill_at", sa.DateTime(timezone=True)),
        )
    if "refilling" not in columns:
        op.add_column(
            "keyword_candidate_pool",
            sa.Column(
                "refilling", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )


def downgrade():
    op.drop_column("keyword_candidate_pool", "refilling")
    op.drop_column("keyword_candidate_pool", "last_refill_at")
