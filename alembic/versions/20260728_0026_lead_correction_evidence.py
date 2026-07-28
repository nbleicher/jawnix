"""Ground every Lead Correction in the evidence it overrode."""

import sqlalchemy as sa
from alembic import op


revision = "20260728_0026"
down_revision = "20260728_0025"
branch_labels = None
depends_on = None


_COLUMNS = (
    "based_on_kind",
    "based_on_observation_id",
    "based_on_title",
    "based_on_state",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("lead_correction_events")
    }
    if "based_on_kind" in existing:
        return

    # Corrections written before this revision recorded only their own values,
    # so the evidence they overrode is genuinely unrecoverable. 'unknown' says
    # that plainly rather than inventing a source for them.
    op.add_column(
        "lead_correction_events",
        sa.Column(
            "based_on_kind",
            sa.String(length=24),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "lead_correction_events",
        sa.Column("based_on_observation_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "lead_correction_events",
        sa.Column(
            "based_on_title",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "lead_correction_events",
        sa.Column(
            "based_on_state",
            sa.String(length=2),
            nullable=False,
            server_default="",
        ),
    )
    if bind.dialect.name != "postgresql":
        # SQLite cannot ALTER in a constraint or a foreign key. Its schema is
        # built from the model metadata instead, which declares both.
        return
    op.create_foreign_key(
        "fk_lead_correction_based_on_observation",
        "lead_correction_events",
        "listing_observations",
        ["based_on_observation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_lead_correction_events_based_on_observation_id",
        "lead_correction_events",
        ["based_on_observation_id"],
    )
    op.create_check_constraint(
        "ck_lead_correction_based_on_kind",
        "lead_correction_events",
        (
            "based_on_kind IN ('current_listing', 'legacy_snapshot', "
            "'prior_correction', 'none', 'unknown')"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("lead_correction_events")
    }
    if "based_on_kind" not in existing:
        return
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "ck_lead_correction_based_on_kind",
            "lead_correction_events",
            type_="check",
        )
        op.drop_index(
            "ix_lead_correction_events_based_on_observation_id",
            table_name="lead_correction_events",
        )
        op.drop_constraint(
            "fk_lead_correction_based_on_observation",
            "lead_correction_events",
            type_="foreignkey",
        )
    for column in _COLUMNS:
        op.drop_column("lead_correction_events", column)
