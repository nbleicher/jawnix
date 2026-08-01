"""Persist Jawnix-owned keyword generation drafts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260731_0031"
down_revision = "20260731_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "keyword_generation_drafts" in tables:
        return
    op.create_table(
        "keyword_generation_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("administrator_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("seed_keyword", sa.String(length=200), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("exclusion_metrics", sa.JSON(), nullable=False),
        sa.Column("candidate_metrics", sa.JSON(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("acceptance_status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "mode IN ('broad', 'adjacent')",
            name="ck_keyword_generation_drafts_mode",
        ),
        sa.CheckConstraint(
            "acceptance_status IN ('pending', 'accepted')",
            name="ck_keyword_generation_drafts_acceptance_status",
        ),
        sa.CheckConstraint(
            "excluded_count >= 0",
            name="ck_keyword_generation_drafts_excluded_count",
        ),
        sa.CheckConstraint(
            "created_at < expires_at",
            name="ck_keyword_generation_drafts_expiry",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "administrator_id",
        "acceptance_status",
        "created_at",
        "expires_at",
    ):
        op.create_index(
            f"ix_keyword_generation_drafts_{column}",
            "keyword_generation_drafts",
            [column],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "keyword_generation_drafts" not in tables:
        return
    for column in (
        "expires_at",
        "created_at",
        "acceptance_status",
        "administrator_id",
    ):
        op.drop_index(
            f"ix_keyword_generation_drafts_{column}",
            table_name="keyword_generation_drafts",
        )
    op.drop_table("keyword_generation_drafts")
