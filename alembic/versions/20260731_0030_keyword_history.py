"""Add Jawnix-owned normalized keyword history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260731_0030"
down_revision = "20260729_0029"
branch_labels = None
depends_on = None


ID_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "keyword_history" not in tables:
        op.create_table(
            "keyword_history",
            sa.Column("id", ID_TYPE, autoincrement=True, nullable=False),
            sa.Column("term", sa.String(length=320), nullable=False),
            sa.Column("origin", sa.String(length=40), nullable=False),
            sa.Column(
                "first_seen_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "last_seen_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.CheckConstraint(
                "origin IN ('legacy_enqueue_log', "
                "'legacy_keyword_history', 'legacy_businesses', "
                "'active_list', 'winner', 'accepted_save')",
                name="ck_keyword_history_origin",
            ),
            sa.CheckConstraint(
                "first_seen_at <= last_seen_at",
                name="ck_keyword_history_seen_range",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "term",
                "origin",
                name="uq_keyword_history_term_origin",
            ),
        )
        for column in (
            "term",
            "origin",
            "first_seen_at",
            "last_seen_at",
        ):
            op.create_index(
                f"ix_keyword_history_{column}",
                "keyword_history",
                [column],
            )

    if "keyword_history_imports" not in tables:
        op.create_table(
            "keyword_history_imports",
            sa.Column("id", ID_TYPE, autoincrement=True, nullable=False),
            sa.Column("source_path", sa.Text(), nullable=False),
            sa.Column("checksum", sa.String(length=64), nullable=False),
            sa.Column("report", sa.JSON(), nullable=False),
            sa.Column(
                "completed_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "checksum",
                name="uq_keyword_history_imports_checksum",
            ),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "keyword_history_imports" in tables:
        op.drop_table("keyword_history_imports")
    if "keyword_history" in tables:
        for column in (
            "last_seen_at",
            "first_seen_at",
            "origin",
            "term",
        ):
            op.drop_index(
                f"ix_keyword_history_{column}",
                table_name="keyword_history",
            )
        op.drop_table("keyword_history")
