"""Add per-Customer prepaid credit billing and Batch Holds."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0036"
down_revision = "20260803_0035"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    if "billing_enabled" not in _columns("agents"):
        op.add_column(
            "agents",
            sa.Column(
                "billing_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "lead_rate_cents_per_thousand" not in _columns("agents"):
        op.add_column(
            "agents",
            sa.Column(
                "lead_rate_cents_per_thousand",
                sa.Integer(),
                nullable=True,
            ),
        )

    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_agent_billing_rate_required",
            "agents",
            (
                "NOT billing_enabled OR "
                "lead_rate_cents_per_thousand IS NOT NULL"
            ),
        )
        op.create_check_constraint(
            "ck_agent_lead_rate_range",
            "agents",
            (
                "lead_rate_cents_per_thousand IS NULL OR "
                "lead_rate_cents_per_thousand BETWEEN 100 AND 2000"
            ),
        )

    request_columns = _columns("lead_requests")
    if "is_billed" not in request_columns:
        op.add_column(
            "lead_requests",
            sa.Column(
                "is_billed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "lead_rate_cents_per_thousand" not in request_columns:
        op.add_column(
            "lead_requests",
            sa.Column(
                "lead_rate_cents_per_thousand",
                sa.Integer(),
                nullable=True,
            ),
        )
    if "billing_amount_cents" not in request_columns:
        op.add_column(
            "lead_requests",
            sa.Column("billing_amount_cents", sa.Integer(), nullable=True),
        )
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_lead_request_frozen_billing",
            "lead_requests",
            (
                "NOT is_billed OR "
                "(lead_rate_cents_per_thousand IS NOT NULL AND "
                "billing_amount_cents IS NOT NULL AND "
                "billing_amount_cents >= 0)"
            ),
        )

    op.create_table(
        "credit_ledger_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_user_id", sa.String(length=160), nullable=False),
        sa.Column("batch_request_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('purchase', 'batch_charge', 'admin_adjustment')",
            name="ck_credit_ledger_kind",
        ),
        sa.CheckConstraint(
            "kind != 'purchase' OR amount_cents > 0",
            name="ck_credit_purchase_positive",
        ),
        sa.CheckConstraint(
            "kind != 'batch_charge' OR amount_cents <= 0",
            name="ck_credit_batch_charge_nonpositive",
        ),
        sa.CheckConstraint(
            "kind != 'batch_charge' OR batch_request_id IS NOT NULL",
            name="ck_credit_batch_charge_request",
        ),
        sa.CheckConstraint(
            (
                "kind != 'admin_adjustment' OR "
                "(amount_cents != 0 AND length(trim(reason)) > 0)"
            ),
            name="ck_credit_adjustment_reason",
        ),
        sa.ForeignKeyConstraint(
            ["batch_request_id"],
            ["lead_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["agents.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_request_id"),
    )
    op.create_index(
        "ix_credit_ledger_entries_customer_id",
        "credit_ledger_entries",
        ["customer_id"],
    )
    op.create_index(
        "ix_credit_ledger_entries_created_at",
        "credit_ledger_entries",
        ["created_at"],
    )
    op.create_index(
        "ix_credit_ledger_customer_created",
        "credit_ledger_entries",
        ["customer_id", "created_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION refuse_credit_ledger_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Credit Ledger entries are append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER credit_ledger_append_only
            BEFORE UPDATE OR DELETE ON credit_ledger_entries
            FOR EACH ROW EXECUTE FUNCTION refuse_credit_ledger_mutation()
            """
        )
    elif op.get_bind().dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER credit_ledger_append_only_update
            BEFORE UPDATE ON credit_ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'Credit Ledger entries are append-only');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER credit_ledger_append_only_delete
            BEFORE DELETE ON credit_ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'Credit Ledger entries are append-only');
            END
            """
        )

    op.create_table(
        "batch_holds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "amount_cents >= 0",
            name="ck_batch_hold_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'captured', 'released')",
            name="ck_batch_hold_status",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["agents.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["lead_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_batch_holds_customer_id",
        "batch_holds",
        ["customer_id"],
    )
    op.create_index(
        "ix_batch_holds_status",
        "batch_holds",
        ["status"],
    )
    op.create_index(
        "ix_batch_hold_customer_status",
        "batch_holds",
        ["customer_id", "status"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER credit_ledger_append_only "
            "ON credit_ledger_entries"
        )
        op.execute("DROP FUNCTION refuse_credit_ledger_mutation()")
    elif op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER credit_ledger_append_only_update")
        op.execute("DROP TRIGGER credit_ledger_append_only_delete")

    op.drop_index(
        "ix_batch_hold_customer_status",
        table_name="batch_holds",
    )
    op.drop_index("ix_batch_holds_status", table_name="batch_holds")
    op.drop_index("ix_batch_holds_customer_id", table_name="batch_holds")
    op.drop_table("batch_holds")

    op.drop_index(
        "ix_credit_ledger_customer_created",
        table_name="credit_ledger_entries",
    )
    op.drop_index(
        "ix_credit_ledger_entries_created_at",
        table_name="credit_ledger_entries",
    )
    op.drop_index(
        "ix_credit_ledger_entries_customer_id",
        table_name="credit_ledger_entries",
    )
    op.drop_table("credit_ledger_entries")

    request_columns = _columns("lead_requests")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "ck_lead_request_frozen_billing",
            "lead_requests",
            type_="check",
        )
    if "billing_amount_cents" in request_columns:
        op.drop_column("lead_requests", "billing_amount_cents")
    if "lead_rate_cents_per_thousand" in request_columns:
        op.drop_column(
            "lead_requests",
            "lead_rate_cents_per_thousand",
        )
    if "is_billed" in request_columns:
        op.drop_column("lead_requests", "is_billed")

    agent_columns = _columns("agents")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "ck_agent_lead_rate_range",
            "agents",
            type_="check",
        )
        op.drop_constraint(
            "ck_agent_billing_rate_required",
            "agents",
            type_="check",
        )
    if "lead_rate_cents_per_thousand" in agent_columns:
        op.drop_column("agents", "lead_rate_cents_per_thousand")
    if "billing_enabled" in agent_columns:
        op.drop_column("agents", "billing_enabled")
