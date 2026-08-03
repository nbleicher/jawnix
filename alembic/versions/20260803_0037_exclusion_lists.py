"""Add typed Exclusion Lists and normalized standing phone bars."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0037"
down_revision = "20260803_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "exclusion_lists" not in tables:
        op.create_table(
            "exclusion_lists",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("customer_id", sa.BigInteger(), nullable=True),
            sa.Column("uploaded_by", sa.String(length=160), nullable=False),
            sa.Column("exclusion_type", sa.String(length=24), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("storage_path", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("global_effective", sa.Boolean(), nullable=False),
            sa.Column("total_rows", sa.Integer(), nullable=False),
            sa.Column("accepted_rows", sa.Integer(), nullable=False),
            sa.Column("invalid_rows", sa.Integer(), nullable=False),
            sa.Column("duplicate_rows", sa.Integer(), nullable=False),
            sa.Column("pool_impact", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("decision_actor", sa.String(length=160), nullable=False),
            sa.Column("decision_reason", sa.Text(), nullable=False),
            sa.Column("nightly_review_id", sa.Uuid(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False
            ),
            sa.Column(
                "ingested_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column(
                "decided_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.CheckConstraint(
                "exclusion_type IN ('landline', 'dnc', 'tcpa_litigator')",
                name="ck_exclusion_lists_type",
            ),
            sa.ForeignKeyConstraint(
                ["customer_id"], ["agents.id"], ondelete="RESTRICT"
            ),
            sa.ForeignKeyConstraint(
                ["nightly_review_id"],
                ["nightly_reviews.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in (
            "customer_id",
            "exclusion_type",
            "status",
            "global_effective",
            "nightly_review_id",
            "created_at",
        ):
            op.create_index(
                f"ix_exclusion_lists_{column}",
                "exclusion_lists",
                [column],
            )
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "exclusion_phones" not in tables:
        op.create_table(
            "exclusion_phones",
            sa.Column("exclusion_list_id", sa.Uuid(), nullable=False),
            sa.Column("phone", sa.String(length=10), nullable=False),
            sa.ForeignKeyConstraint(
                ["exclusion_list_id"],
                ["exclusion_lists.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("exclusion_list_id", "phone"),
        )
        op.create_index(
            "ix_exclusion_phones_phone",
            "exclusion_phones",
            ["phone"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "exclusion_phones" in tables:
        op.drop_index(
            "ix_exclusion_phones_phone",
            table_name="exclusion_phones",
        )
        op.drop_table("exclusion_phones")
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "exclusion_lists" in tables:
        for column in (
            "created_at",
            "nightly_review_id",
            "global_effective",
            "status",
            "exclusion_type",
            "customer_id",
        ):
            op.drop_index(
                f"ix_exclusion_lists_{column}",
                table_name="exclusion_lists",
            )
        op.drop_table("exclusion_lists")
