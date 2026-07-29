"""Retain Agency membership and merge permanent no-repeat history."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "20260729_0027"
down_revision = "20260728_0026"
branch_labels = None
depends_on = None

_ID_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _set_not_null(table: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "permanent_history_key",
                existing_type=sa.String(length=64),
                nullable=False,
            )
    else:
        op.alter_column(
            table,
            "permanent_history_key",
            existing_type=sa.String(length=64),
            nullable=False,
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    agency_columns = {
        column["name"] for column in inspector.get_columns("agencies")
    }
    customer_columns = {
        column["name"] for column in inspector.get_columns("agents")
    }
    if "permanent_history_key" not in agency_columns:
        op.add_column(
            "agencies",
            sa.Column(
                "permanent_history_key",
                sa.String(length=64),
                nullable=True,
            ),
        )
    if "permanent_history_key" not in customer_columns:
        op.add_column(
            "agents",
            sa.Column(
                "permanent_history_key",
                sa.String(length=64),
                nullable=True,
            ),
        )

    # Every existing current Agency component starts as one permanent
    # no-repeat component. Standalone Customers each start independently.
    agency_rows = bind.execute(
        sa.text(
            "SELECT id FROM agencies "
            "WHERE permanent_history_key IS NULL ORDER BY id"
        )
    ).all()
    for (agency_id,) in agency_rows:
        key = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "UPDATE agencies SET permanent_history_key = :key "
                "WHERE id = :agency_id"
            ),
            {"key": key, "agency_id": agency_id},
        )
        bind.execute(
            sa.text(
                "UPDATE agents SET permanent_history_key = :key "
                "WHERE agency_id = :agency_id "
                "AND permanent_history_key IS NULL"
            ),
            {"key": key, "agency_id": agency_id},
        )
    standalone_rows = bind.execute(
        sa.text(
            "SELECT id FROM agents "
            "WHERE permanent_history_key IS NULL ORDER BY id"
        )
    ).all()
    for (customer_id,) in standalone_rows:
        bind.execute(
            sa.text(
                "UPDATE agents SET permanent_history_key = :key "
                "WHERE id = :customer_id"
            ),
            {
                "key": str(uuid.uuid4()),
                "customer_id": customer_id,
            },
        )

    inspector = sa.inspect(bind)
    if not any(
        index["name"] == "ix_agencies_permanent_history_key"
        for index in inspector.get_indexes("agencies")
    ):
        op.create_index(
            "ix_agencies_permanent_history_key",
            "agencies",
            ["permanent_history_key"],
        )
    if not any(
        index["name"] == "ix_agents_permanent_history_key"
        for index in inspector.get_indexes("agents")
    ):
        op.create_index(
            "ix_agents_permanent_history_key",
            "agents",
            ["permanent_history_key"],
        )
    _set_not_null("agencies")
    _set_not_null("agents")

    inspector = sa.inspect(bind)
    if "agency_membership_history" in inspector.get_table_names():
        return
    op.create_table(
        "agency_membership_history",
        sa.Column("id", _ID_TYPE, autoincrement=True, nullable=False),
        sa.Column("customer_id", _ID_TYPE, nullable=False),
        sa.Column("agency_id", _ID_TYPE, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_by", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agency_id"],
            ["agencies.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["agents.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agency_membership_history_customer_id",
        "agency_membership_history",
        ["customer_id"],
    )
    op.create_index(
        "ix_agency_membership_history_agency_id",
        "agency_membership_history",
        ["agency_id"],
    )
    op.create_index(
        "ix_agency_membership_history_ended_at",
        "agency_membership_history",
        ["ended_at"],
    )
    where = sa.text("ended_at IS NULL")
    predicate = (
        {"postgresql_where": where}
        if bind.dialect.name == "postgresql"
        else {"sqlite_where": where}
    )
    op.create_index(
        "uq_current_agency_membership_per_customer",
        "agency_membership_history",
        ["customer_id"],
        unique=True,
        **predicate,
    )
    now = datetime.now(timezone.utc)
    current_members = bind.execute(
        sa.text(
            "SELECT id, agency_id FROM agents "
            "WHERE agency_id IS NOT NULL ORDER BY id"
        )
    ).all()
    for customer_id, agency_id in current_members:
        bind.execute(
            sa.text(
                "INSERT INTO agency_membership_history "
                "(customer_id, agency_id, started_at, ended_at, "
                "assigned_by, reason) "
                "VALUES (:customer_id, :agency_id, :started_at, NULL, "
                ":assigned_by, :reason)"
            ),
            {
                "customer_id": customer_id,
                "agency_id": agency_id,
                "started_at": now,
                "assigned_by": "system:migration",
                "reason": (
                    "Existing Agency membership retained by migration."
                ),
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agency_membership_history" in inspector.get_table_names():
        for index in (
            "uq_current_agency_membership_per_customer",
            "ix_agency_membership_history_ended_at",
            "ix_agency_membership_history_agency_id",
            "ix_agency_membership_history_customer_id",
        ):
            op.drop_index(index, table_name="agency_membership_history")
        op.drop_table("agency_membership_history")

    inspector = sa.inspect(bind)
    agency_columns = {
        column["name"] for column in inspector.get_columns("agencies")
    }
    customer_columns = {
        column["name"] for column in inspector.get_columns("agents")
    }
    if "permanent_history_key" in customer_columns:
        op.drop_index(
            "ix_agents_permanent_history_key",
            table_name="agents",
        )
        op.drop_column("agents", "permanent_history_key")
    if "permanent_history_key" in agency_columns:
        op.drop_index(
            "ix_agencies_permanent_history_key",
            table_name="agencies",
        )
        op.drop_column("agencies", "permanent_history_key")
