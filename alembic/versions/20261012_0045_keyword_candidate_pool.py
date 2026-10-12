"""Retain automatic keyword candidates between generation runs."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20261012_0045"
down_revision = "20260804_0044"
branch_labels = None
depends_on = None


def upgrade():
    # The initial revision creates tables from the current models on an empty DB.
    if not sa.inspect(op.get_bind()).has_table("keyword_candidate_pool"):
        op.create_table(
            "keyword_candidate_pool",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("candidates", sa.JSON(), nullable=False),
            sa.Column("focus_index", sa.Integer(), nullable=False),
            sa.Column("next_refill_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_keyword_candidate_pool_singleton"),
            sa.CheckConstraint(
                "focus_index >= 0", name="ck_keyword_candidate_pool_focus"
            ),
        )
    table = sa.table(
        "keyword_candidate_pool",
        sa.column("id", sa.Integer()),
        sa.column("candidates", sa.JSON()),
        sa.column("focus_index", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    if not op.get_bind().scalar(sa.select(table.c.id).where(table.c.id == 1)):
        op.get_bind().execute(
            table.insert().values(
                id=1, candidates=[], focus_index=0, updated_at=sa.func.now()
            )
        )


def downgrade():
    op.drop_table("keyword_candidate_pool")
