"""Add Lead Reports, artifact expiry, and distribution periods."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0017"
down_revision = "20260726_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    distribution_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "distribution_events"
        )
    }
    if "distribution_period" not in distribution_columns:
        op.add_column(
            "distribution_events",
            sa.Column(
                "distribution_period",
                sa.String(length=7),
                nullable=False,
                server_default="",
            ),
        )
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                """
                UPDATE distribution_events
                SET distribution_period =
                    to_char(delivered_at AT TIME ZONE 'UTC', 'YYYY-MM')
                """
            )
        else:
            op.execute(
                """
                UPDATE distribution_events
                SET distribution_period = strftime('%Y-%m', delivered_at)
                """
            )
        op.create_index(
            "ix_distribution_events_distribution_period",
            "distribution_events",
            ["distribution_period"],
        )
    artifact_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("batch_artifacts")
    }
    if "expires_at" not in artifact_columns:
        op.add_column(
            "batch_artifacts",
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                """
                UPDATE batch_artifacts
                SET expires_at = created_at + interval '30 days'
                """
            )
        else:
            op.execute(
                """
                UPDATE batch_artifacts
                SET expires_at = datetime(created_at, '+30 days')
                """
            )
        op.create_index(
            "ix_batch_artifacts_expires_at",
            "batch_artifacts",
            ["expires_at"],
        )
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "lead_reports" not in tables:
        op.create_table(
            "lead_reports",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column(
                "distribution_event_id",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column("customer_id", sa.BigInteger(), nullable=False),
            sa.Column("reason", sa.String(length=40), nullable=False),
            sa.Column("details", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
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
            sa.PrimaryKeyConstraint("id"),
        )
        for column in (
            "distribution_event_id",
            "customer_id",
            "reason",
            "status",
            "created_at",
        ):
            op.create_index(
                f"ix_lead_reports_{column}",
                "lead_reports",
                [column],
            )
    if "lead_report_resolutions" not in tables:
        op.create_table(
            "lead_report_resolutions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("report_id", sa.Uuid(), nullable=False),
            sa.Column("action", sa.String(length=24), nullable=False),
            sa.Column("note", sa.Text(), nullable=False),
            sa.Column(
                "actor_id",
                sa.String(length=160),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["report_id"],
                ["lead_reports.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_lead_report_resolutions_action",
            "lead_report_resolutions",
            ["action"],
        )
        op.create_index(
            "ix_lead_report_resolutions_report_id",
            "lead_report_resolutions",
            ["report_id"],
            unique=True,
        )


def downgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "lead_report_resolutions" in tables:
        op.drop_index(
            "ix_lead_report_resolutions_report_id",
            table_name="lead_report_resolutions",
        )
        op.drop_index(
            "ix_lead_report_resolutions_action",
            table_name="lead_report_resolutions",
        )
        op.drop_table("lead_report_resolutions")
    if "lead_reports" in tables:
        for column in (
            "created_at",
            "status",
            "reason",
            "customer_id",
            "distribution_event_id",
        ):
            op.drop_index(
                f"ix_lead_reports_{column}",
                table_name="lead_reports",
            )
        op.drop_table("lead_reports")
    artifact_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("batch_artifacts")
    }
    if "expires_at" in artifact_columns:
        op.drop_index(
            "ix_batch_artifacts_expires_at",
            table_name="batch_artifacts",
        )
        op.drop_column("batch_artifacts", "expires_at")
    distribution_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "distribution_events"
        )
    }
    if "distribution_period" in distribution_columns:
        op.drop_index(
            "ix_distribution_events_distribution_period",
            table_name="distribution_events",
        )
        op.drop_column("distribution_events", "distribution_period")
