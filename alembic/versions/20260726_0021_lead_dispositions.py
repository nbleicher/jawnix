"""Add append-only Lead Disposition history."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0021"
down_revision = "20260726_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if (
        "lead_disposition_transitions"
        not in inspector.get_table_names()
    ):
        op.create_table(
            "lead_disposition_transitions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column(
                "distribution_event_id",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column(
                "customer_id",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column(
                "actor_user_id",
                sa.Uuid(),
                nullable=False,
            ),
            sa.Column(
                "disposition",
                sa.String(length=40),
                nullable=False,
            ),
            sa.Column("note", sa.Text(), nullable=False),
            sa.Column(
                "previous_transition_id",
                sa.Uuid(),
                nullable=True,
            ),
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
            sa.ForeignKeyConstraint(
                ["previous_transition_id"],
                ["lead_disposition_transitions.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                (
                    "disposition IN ('no_contact', "
                    "'not_interested', 'positive_response', "
                    "'appointment_booked', "
                    "'appointment_canceled', "
                    "'appointment_no_show', 'invalid_phone', "
                    "'wrong_business', 'do_not_contact', 'other')"
                ),
                name="ck_lead_disposition_transition_value",
            ),
            sa.UniqueConstraint(
                "previous_transition_id",
                name=(
                    "uq_lead_disposition_transition_previous"
                ),
            ),
        )
        for column in (
            "actor_user_id",
            "created_at",
            "customer_id",
            "disposition",
            "distribution_event_id",
            "previous_transition_id",
        ):
            op.create_index(
                f"ix_lead_disposition_transitions_{column}",
                "lead_disposition_transitions",
                [column],
            )
    inspector = sa.inspect(op.get_bind())
    if "lead_disposition_states" not in inspector.get_table_names():
        op.create_table(
            "lead_disposition_states",
            sa.Column(
                "distribution_event_id",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column(
                "current_transition_id",
                sa.Uuid(),
                nullable=False,
            ),
            sa.Column(
                "current_disposition",
                sa.String(length=40),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["current_transition_id"],
                ["lead_disposition_transitions.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["distribution_event_id"],
                ["distribution_events.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("distribution_event_id"),
            sa.UniqueConstraint("current_transition_id"),
        )
        op.create_index(
            "ix_lead_disposition_states_current_disposition",
            "lead_disposition_states",
            ["current_disposition"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "lead_disposition_states" in inspector.get_table_names():
        op.drop_index(
            "ix_lead_disposition_states_current_disposition",
            table_name="lead_disposition_states",
        )
        op.drop_table("lead_disposition_states")
    inspector = sa.inspect(op.get_bind())
    if (
        "lead_disposition_transitions"
        in inspector.get_table_names()
    ):
        for column in (
            "previous_transition_id",
            "distribution_event_id",
            "disposition",
            "customer_id",
            "created_at",
            "actor_user_id",
        ):
            op.drop_index(
                f"ix_lead_disposition_transitions_{column}",
                table_name="lead_disposition_transitions",
            )
        op.drop_table("lead_disposition_transitions")
