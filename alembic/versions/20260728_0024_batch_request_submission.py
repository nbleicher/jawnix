"""Add Batch Request submission keys and a stopped-at timestamp."""

import sqlalchemy as sa
from alembic import op


revision = "20260728_0024"
down_revision = "20260728_0023"
branch_labels = None
depends_on = None


def _columns(bind) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(bind).get_columns("lead_requests")
    }


def _indexes(bind) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(bind).get_indexes("lead_requests")
    }


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if "closed_at" not in columns:
        op.add_column(
            "lead_requests",
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "idempotency_key" not in columns:
        op.add_column(
            "lead_requests",
            sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        )
    if "uq_lead_request_idempotency" not in _indexes(bind):
        # A unique index rather than a unique constraint: SQLite cannot ALTER
        # a constraint onto an existing table, and NULL keys stay distinct
        # either way, so every Batch Request written before this column
        # existed remains valid.
        op.create_index(
            "uq_lead_request_idempotency",
            "lead_requests",
            ["user_id", "idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "uq_lead_request_idempotency" in _indexes(bind):
        op.drop_index(
            "uq_lead_request_idempotency",
            table_name="lead_requests",
        )
    columns = _columns(bind)
    if "idempotency_key" in columns:
        op.drop_column("lead_requests", "idempotency_key")
    if "closed_at" in columns:
        op.drop_column("lead_requests", "closed_at")
