"""Persist human-approved Source Recommendations."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0018"
down_revision = "20260726_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if (
        "source_recommendations"
        in sa.inspect(op.get_bind()).get_table_names()
    ):
        return
    op.create_table(
        "source_recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("niche", sa.String(length=160), nullable=False),
        sa.Column(
            "segment_key",
            sa.String(length=320),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column(
            "evidence_checksum",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "decision_by",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "resulting_configuration_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["resulting_configuration_id"],
            ["scraper_configurations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_checksum"),
    )
    for column in (
        "niche",
        "segment_key",
        "action",
        "status",
        "resulting_configuration_id",
        "created_at",
    ):
        op.create_index(
            f"ix_source_recommendations_{column}",
            "source_recommendations",
            [column],
        )


def downgrade() -> None:
    if (
        "source_recommendations"
        not in sa.inspect(op.get_bind()).get_table_names()
    ):
        return
    for column in (
        "created_at",
        "resulting_configuration_id",
        "status",
        "action",
        "segment_key",
        "niche",
    ):
        op.drop_index(
            f"ix_source_recommendations_{column}",
            table_name="source_recommendations",
        )
    op.drop_table("source_recommendations")
