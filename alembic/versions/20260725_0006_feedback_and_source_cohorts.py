"""Add append-only feedback and immutable Source Cohort snapshots."""

import sqlalchemy as sa
from alembic import op


revision = "20260725_0006"
down_revision = "20260725_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "lead_outcomes" in inspector.get_table_names():
        return
    op.add_column(
        "distribution_events",
        sa.Column(
            "source_kind",
            sa.String(length=32),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "distribution_events",
        sa.Column(
            "source_segment_key",
            sa.String(length=320),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "distribution_events",
        sa.Column(
            "source_niche",
            sa.String(length=160),
            nullable=False,
            server_default="",
        ),
    )
    op.create_index(
        "ix_distribution_events_source_kind",
        "distribution_events",
        ["source_kind"],
    )
    op.create_index(
        "ix_distribution_events_source_segment_key",
        "distribution_events",
        ["source_segment_key"],
    )
    op.create_index(
        "ix_distribution_events_source_niche",
        "distribution_events",
        ["source_niche"],
    )
    op.create_table(
        "lead_outcomes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("distribution_event_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("appointment_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("supersedes_outcome_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["agents.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["distribution_event_id"],
            ["distribution_events.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_outcome_id"],
            ["lead_outcomes.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lead_outcomes_created_at",
        "lead_outcomes",
        ["created_at"],
    )
    op.create_index(
        "ix_lead_outcomes_customer_id",
        "lead_outcomes",
        ["customer_id"],
    )
    op.create_index(
        "ix_lead_outcomes_distribution_event_id",
        "lead_outcomes",
        ["distribution_event_id"],
    )
    op.create_index(
        "ix_lead_outcomes_supersedes_outcome_id",
        "lead_outcomes",
        ["supersedes_outcome_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lead_outcomes_supersedes_outcome_id",
        table_name="lead_outcomes",
    )
    op.drop_index(
        "ix_lead_outcomes_distribution_event_id",
        table_name="lead_outcomes",
    )
    op.drop_index("ix_lead_outcomes_customer_id", table_name="lead_outcomes")
    op.drop_index("ix_lead_outcomes_created_at", table_name="lead_outcomes")
    op.drop_table("lead_outcomes")
    op.drop_index(
        "ix_distribution_events_source_niche",
        table_name="distribution_events",
    )
    op.drop_index(
        "ix_distribution_events_source_segment_key",
        table_name="distribution_events",
    )
    op.drop_index(
        "ix_distribution_events_source_kind",
        table_name="distribution_events",
    )
    op.drop_column("distribution_events", "source_niche")
    op.drop_column("distribution_events", "source_segment_key")
    op.drop_column("distribution_events", "source_kind")
