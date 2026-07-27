"""Add Source Segment optimization, reports, and nightly review controls."""

import sqlalchemy as sa
from alembic import op


revision = "20260727_0022"
down_revision = "20260726_0021"
branch_labels = None
depends_on = None


def _index(table: str, column: str) -> None:
    name = f"ix_{table}_{column}"
    indexes = sa.inspect(op.get_bind()).get_indexes(table)
    if not any(item["name"] == name for item in indexes):
        op.create_index(name, table, [column])


def _has_unique(table: str, columns: set[str]) -> bool:
    return any(
        set(item["column_names"]) == columns
        for item in sa.inspect(op.get_bind()).get_unique_constraints(table)
    )


def _has_foreign_key(table: str, columns: set[str]) -> bool:
    return any(
        set(item["constrained_columns"]) == columns
        for item in sa.inspect(op.get_bind()).get_foreign_keys(table)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    report_columns = {
        column["name"]
        for column in inspector.get_columns("lead_reports")
    }
    outcome_columns = {
        column["name"]
        for column in inspector.get_columns("lead_outcomes")
    }
    if "actor_user_id" not in outcome_columns:
        op.add_column(
            "lead_outcomes",
            sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        )
        _index("lead_outcomes", "actor_user_id")
    transition_columns = {
        column["name"]
        for column in inspector.get_columns(
            "lead_disposition_transitions"
        )
    }
    if "source_outcome_id" not in transition_columns:
        op.add_column(
            "lead_disposition_transitions",
            sa.Column("source_outcome_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "fk_disposition_transition_source_outcome",
            "lead_disposition_transitions",
            "lead_outcomes",
            ["source_outcome_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_unique_constraint(
            "uq_disposition_transition_source_outcome",
            "lead_disposition_transitions",
            ["source_outcome_id"],
        )
        _index("lead_disposition_transitions", "source_outcome_id")
    if "source_transition_id" not in report_columns:
        op.add_column(
            "lead_reports",
            sa.Column("source_transition_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "fk_lead_reports_source_transition",
            "lead_reports",
            "lead_disposition_transitions",
            ["source_transition_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_unique_constraint(
            "uq_lead_reports_source_transition",
            "lead_reports",
            ["source_transition_id"],
        )
        _index("lead_reports", "source_transition_id")

    if "eligibility_holds" not in tables:
        op.create_table(
            "eligibility_holds",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("lead_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "distribution_event_id",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column("report_id", sa.Uuid(), nullable=False),
            sa.Column("reason", sa.String(length=40), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column(
                "released_by",
                sa.String(length=160),
                nullable=False,
            ),
            sa.Column("release_reason", sa.Text(), nullable=False),
            sa.Column(
                "released_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["distribution_event_id"],
                ["distribution_events.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["lead_id"],
                ["lead_inventory.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["report_id"],
                ["lead_reports.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("report_id"),
        )
        for column in (
            "lead_id",
            "distribution_event_id",
            "report_id",
            "reason",
            "active",
        ):
            _index("eligibility_holds", column)

    if "source_niche_mappings" not in tables:
        op.create_table(
            "source_niche_mappings",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column(
                "segment_key",
                sa.String(length=320),
                nullable=False,
            ),
            sa.Column("state", sa.String(length=2), nullable=False),
            sa.Column(
                "keyword",
                sa.String(length=240),
                nullable=False,
            ),
            sa.Column(
                "niche",
                sa.String(length=160),
                nullable=False,
            ),
            sa.Column("confirmed", sa.Boolean(), nullable=False),
            sa.Column(
                "proposal_source",
                sa.String(length=40),
                nullable=False,
            ),
            sa.Column("proposed_evidence", sa.JSON(), nullable=False),
            sa.Column(
                "confirmed_by",
                sa.String(length=160),
                nullable=False,
            ),
            sa.Column(
                "confirmed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("segment_key"),
        )
        for column in (
            "segment_key",
            "state",
            "keyword",
            "niche",
            "confirmed",
        ):
            _index("source_niche_mappings", column)

    review_columns = {
        column["name"]
        for column in inspector.get_columns("nightly_reviews")
    }
    for column in (
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    ):
        if column.name not in review_columns:
            op.add_column("nightly_reviews", column)
    op.alter_column(
        "nightly_reviews",
        "scraper_run_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    if not _has_unique("nightly_reviews", {"review_date"}):
        op.create_unique_constraint(
            "uq_nightly_reviews_review_date",
            "nightly_reviews",
            ["review_date"],
        )
    _index("nightly_reviews", "next_retry_at")

    if "daily_source_performance" not in tables:
        op.create_table(
            "daily_source_performance",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("nightly_review_id", sa.Uuid(), nullable=False),
            sa.Column("snapshot_date", sa.Date(), nullable=False),
            sa.Column(
                "segment_key",
                sa.String(length=320),
                nullable=False,
            ),
            sa.Column("state", sa.String(length=2), nullable=False),
            sa.Column(
                "keyword",
                sa.String(length=240),
                nullable=False,
            ),
            sa.Column(
                "niche",
                sa.String(length=160),
                nullable=False,
            ),
            sa.Column(
                "niche_confirmed",
                sa.Boolean(),
                nullable=False,
            ),
            sa.Column(
                "window_started_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "window_ended_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column("counts", sa.JSON(), nullable=False),
            sa.Column("rates", sa.JSON(), nullable=False),
            sa.Column("intervals", sa.JSON(), nullable=False),
            sa.Column("trend", sa.JSON(), nullable=False),
            sa.Column(
                "eligibility",
                sa.String(length=48),
                nullable=False,
            ),
            sa.Column(
                "action_state",
                sa.String(length=32),
                nullable=False,
            ),
            sa.Column(
                "evidence_checksum",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["nightly_review_id"],
                ["nightly_reviews.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("evidence_checksum"),
            sa.UniqueConstraint(
                "snapshot_date",
                "segment_key",
                name=(
                    "uq_daily_source_performance_date_segment"
                ),
            ),
        )
        for column in (
            "nightly_review_id",
            "snapshot_date",
            "segment_key",
            "state",
            "keyword",
            "niche",
            "niche_confirmed",
            "eligibility",
            "action_state",
        ):
            _index("daily_source_performance", column)

    if "performance_suggestion_notes" not in tables:
        op.create_table(
            "performance_suggestion_notes",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("snapshot_id", sa.Uuid(), nullable=False),
            sa.Column(
                "segment_key",
                sa.String(length=320),
                nullable=False,
            ),
            sa.Column(
                "template_key",
                sa.String(length=80),
                nullable=False,
            ),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column(
                "evidence_checksum",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["snapshot_id"],
                ["daily_source_performance.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("snapshot_id"),
            sa.UniqueConstraint("evidence_checksum"),
        )
        _index("performance_suggestion_notes", "snapshot_id")
        _index("performance_suggestion_notes", "segment_key")

    recommendation_columns = {
        column["name"]
        for column in inspector.get_columns("source_recommendations")
    }
    recommendation_additions = (
        sa.Column("nightly_review_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column(
            "configuration_version",
            sa.Integer(),
            nullable=True,
        ),
    )
    for column in recommendation_additions:
        if column.name not in recommendation_columns:
            op.add_column("source_recommendations", column)
    if not _has_foreign_key(
        "source_recommendations",
        {"nightly_review_id"},
    ):
        op.create_foreign_key(
            "fk_source_recommendations_nightly_review",
            "source_recommendations",
            "nightly_reviews",
            ["nightly_review_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    if not _has_foreign_key(
        "source_recommendations",
        {"snapshot_id"},
    ):
        op.create_foreign_key(
            "fk_source_recommendations_snapshot",
            "source_recommendations",
            "daily_source_performance",
            ["snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    _index("source_recommendations", "nightly_review_id")
    _index("source_recommendations", "snapshot_id")

    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                INSERT INTO lead_disposition_transitions (
                    id,
                    distribution_event_id,
                    customer_id,
                    actor_user_id,
                    source_outcome_id,
                    disposition,
                    note,
                    previous_transition_id,
                    created_at
                )
                SELECT
                    gen_random_uuid(),
                    outcome.distribution_event_id,
                    outcome.customer_id,
                    outcome.actor_user_id,
                    outcome.id,
                    outcome.kind,
                    outcome.note,
                    NULL,
                    outcome.created_at
                FROM lead_outcomes AS outcome
                WHERE outcome.kind IN (
                    'positive_response',
                    'appointment_booked'
                )
                  AND outcome.actor_user_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM lead_disposition_transitions AS transition
                    WHERE transition.source_outcome_id = outcome.id
                  )
                """
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO lead_disposition_states (
                    distribution_event_id,
                    current_transition_id,
                    current_disposition,
                    updated_at
                )
                SELECT DISTINCT ON (transition.distribution_event_id)
                    transition.distribution_event_id,
                    transition.id,
                    transition.disposition,
                    transition.created_at
                FROM lead_disposition_transitions AS transition
                LEFT JOIN lead_disposition_states AS state
                  ON state.distribution_event_id
                   = transition.distribution_event_id
                WHERE state.distribution_event_id IS NULL
                ORDER BY
                    transition.distribution_event_id,
                    transition.created_at DESC,
                    transition.id DESC
                """
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO lead_reports (
                    id,
                    distribution_event_id,
                    customer_id,
                    source_transition_id,
                    reason,
                    details,
                    status,
                    created_at
                )
                SELECT
                    gen_random_uuid(),
                    transition.distribution_event_id,
                    transition.customer_id,
                    transition.id,
                    CASE transition.disposition
                        WHEN 'invalid_phone' THEN 'invalid_phone'
                        WHEN 'wrong_business'
                            THEN 'wrong_business_or_title'
                        WHEN 'do_not_contact'
                            THEN 'do_not_contact_or_legal'
                    END,
                    transition.note,
                    'open',
                    transition.created_at
                FROM lead_disposition_transitions AS transition
                WHERE transition.disposition IN (
                    'invalid_phone',
                    'wrong_business',
                    'do_not_contact'
                )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM lead_reports AS report
                    WHERE report.source_transition_id = transition.id
                  )
                """
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO eligibility_holds (
                    id,
                    lead_id,
                    distribution_event_id,
                    report_id,
                    reason,
                    active,
                    released_by,
                    release_reason,
                    released_at,
                    created_at
                )
                SELECT
                    gen_random_uuid(),
                    event.lead_id,
                    report.distribution_event_id,
                    report.id,
                    transition.disposition,
                    true,
                    '',
                    '',
                    NULL,
                    report.created_at
                FROM lead_reports AS report
                JOIN lead_disposition_transitions AS transition
                  ON transition.id = report.source_transition_id
                JOIN distribution_events AS event
                  ON event.id = report.distribution_event_id
                WHERE transition.disposition IN (
                    'invalid_phone',
                    'do_not_contact'
                )
                  AND report.status = 'open'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM eligibility_holds AS hold
                    WHERE hold.report_id = report.id
                  )
                """
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_source_recommendations_snapshot_id",
        table_name="source_recommendations",
    )
    op.drop_index(
        "ix_source_recommendations_nightly_review_id",
        table_name="source_recommendations",
    )
    op.drop_constraint(
        "fk_source_recommendations_snapshot",
        "source_recommendations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_source_recommendations_nightly_review",
        "source_recommendations",
        type_="foreignkey",
    )
    for column in (
        "configuration_version",
        "snapshot_id",
        "nightly_review_id",
    ):
        op.drop_column("source_recommendations", column)
    op.drop_table("performance_suggestion_notes")
    op.drop_table("daily_source_performance")
    op.drop_index(
        "ix_nightly_reviews_next_retry_at",
        table_name="nightly_reviews",
    )
    op.drop_constraint(
        "uq_nightly_reviews_review_date",
        "nightly_reviews",
        type_="unique",
    )
    for column in (
        "next_retry_at",
        "attempt_count",
        "scheduled_for",
        "review_date",
    ):
        op.drop_column("nightly_reviews", column)
    op.alter_column(
        "nightly_reviews",
        "scraper_run_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_table("source_niche_mappings")
    op.drop_table("eligibility_holds")
    op.drop_index(
        "ix_lead_reports_source_transition_id",
        table_name="lead_reports",
    )
    op.drop_constraint(
        "uq_lead_reports_source_transition",
        "lead_reports",
        type_="unique",
    )
    op.drop_constraint(
        "fk_lead_reports_source_transition",
        "lead_reports",
        type_="foreignkey",
    )
    op.drop_column("lead_reports", "source_transition_id")
    op.drop_index(
        "ix_lead_disposition_transitions_source_outcome_id",
        table_name="lead_disposition_transitions",
    )
    op.drop_constraint(
        "uq_disposition_transition_source_outcome",
        "lead_disposition_transitions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_disposition_transition_source_outcome",
        "lead_disposition_transitions",
        type_="foreignkey",
    )
    op.drop_column(
        "lead_disposition_transitions",
        "source_outcome_id",
    )
    op.drop_index(
        "ix_lead_outcomes_actor_user_id",
        table_name="lead_outcomes",
    )
    op.drop_column("lead_outcomes", "actor_user_id")
