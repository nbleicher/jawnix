"""Clean inventory (DB3): per-database memberships with expiry.

A Lead can now hold one membership per upload-backed database, so a phone can
sell from DB2 and DB3 at once. DB3 memberships carry ``expires_on`` (each
row's scrub date plus 30 days); DB2 memberships never expire. DB3 uploads may
record the form's fallback scrub date on ``lead_uploads.scrubbed_on``; each
row's own Scrubbed On column wins. Batch Requests record whether they draw
Clean (DB3) inventory, and Customer Availability Snapshots key by database so
Standard and Clean availability are cached apart.

The 0055 baseline builds fresh schemas from the current models, so every step
here is guarded.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20261119_0057"
down_revision = "20261112_0056"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {row["name"] for row in inspector.get_columns(table)}


def _checks(inspector, table: str) -> set[str]:
    return {row["name"] for row in inspector.get_check_constraints(table)}


def _check_sql(inspector, table: str, name: str) -> str:
    for row in inspector.get_check_constraints(table):
        if row["name"] == name:
            return row["sqltext"]
    return ""


def _indexes(inspector, table: str) -> set[str]:
    return {row["name"] for row in inspector.get_indexes(table)}


def _pk_columns(inspector, table: str) -> list[str]:
    return list(inspector.get_pk_constraint(table).get("constrained_columns") or [])


def _nullable(inspector, table: str, column: str) -> bool:
    for row in inspector.get_columns(table):
        if row["name"] == column:
            return bool(row["nullable"])
    return True


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    with op.batch_alter_table("lead_uploads") as batch:
        columns = _columns(inspector, "lead_uploads")
        if "inventory_database" not in columns:
            batch.add_column(
                sa.Column(
                    "inventory_database",
                    sa.String(length=4),
                    server_default="db2",
                    nullable=False,
                )
            )
        if "scrubbed_on" not in columns:
            batch.add_column(sa.Column("scrubbed_on", sa.Date(), nullable=True))
        checks = _checks(inspector, "lead_uploads")
        if "ck_lead_upload_inventory_database" not in checks:
            batch.create_check_constraint(
                "ck_lead_upload_inventory_database",
                "inventory_database IN ('db2', 'db3')",
            )
        if "ck_lead_upload_scrubbed_on" not in checks:
            # DB2 uploads never carry a scrub date. DB3 uploads may: the form
            # date is the fallback for rows without a Scrubbed On column.
            batch.create_check_constraint(
                "ck_lead_upload_scrubbed_on",
                "inventory_database = 'db3' OR scrubbed_on IS NULL",
            )

    with op.batch_alter_table("lead_database_memberships") as batch:
        columns = _columns(inspector, "lead_database_memberships")
        if "inventory_database" not in columns:
            batch.add_column(
                sa.Column(
                    "inventory_database",
                    sa.String(length=4),
                    server_default="db2",
                    nullable=False,
                )
            )
        if "expires_on" not in columns:
            batch.add_column(sa.Column("expires_on", sa.Date(), nullable=True))
        if _pk_columns(inspector, "lead_database_memberships") != ["inventory_database", "lead_id"]:
            batch.drop_constraint("lead_database_memberships_pkey", type_="primary")
            batch.create_primary_key("lead_database_memberships_pkey", ["inventory_database", "lead_id"])
        if "ck_lead_database_membership_expiry" not in _checks(inspector, "lead_database_memberships"):
            batch.create_check_constraint(
                "ck_lead_database_membership_expiry",
                "(inventory_database = 'db3') = (expires_on IS NOT NULL)",
            )
    indexes = _indexes(sa.inspect(op.get_bind()), "lead_database_memberships")
    if "ix_lead_database_memberships_database_state" not in indexes:
        op.create_index(
            "ix_lead_database_memberships_database_state",
            "lead_database_memberships",
            ["inventory_database", "state"],
        )
    if "ix_lead_database_memberships_expires_on" not in indexes:
        op.create_index(
            "ix_lead_database_memberships_expires_on",
            "lead_database_memberships",
            ["expires_on"],
            postgresql_where=sa.text("inventory_database = 'db3'"),
        )

    if "clean" not in _columns(inspector, "lead_requests"):
        with op.batch_alter_table("lead_requests") as batch:
            batch.add_column(
                sa.Column("clean", sa.Boolean(), server_default=sa.false(), nullable=False)
            )

    with op.batch_alter_table("customer_availability_snapshots") as batch:
        if "inventory_database" not in _columns(inspector, "customer_availability_snapshots"):
            batch.add_column(sa.Column("inventory_database", sa.String(length=4), nullable=True))
    op.execute(
        "UPDATE customer_availability_snapshots s SET inventory_database = a.lead_database "
        "FROM agents a WHERE a.id = s.customer_id AND s.inventory_database IS NULL"
    )
    with op.batch_alter_table("customer_availability_snapshots") as batch:
        if _nullable(inspector, "customer_availability_snapshots", "inventory_database"):
            batch.alter_column("inventory_database", nullable=False)
        if _pk_columns(inspector, "customer_availability_snapshots") != ["customer_id", "inventory_database"]:
            batch.drop_constraint("customer_availability_snapshots_pkey", type_="primary")
            batch.create_primary_key(
                "customer_availability_snapshots_pkey", ["customer_id", "inventory_database"]
            )

    if "db3" not in _check_sql(inspector, "inventory_snapshots", "ck_inventory_snapshot_database"):
        with op.batch_alter_table("inventory_snapshots") as batch:
            batch.drop_constraint("ck_inventory_snapshot_database", type_="check")
            batch.create_check_constraint(
                "ck_inventory_snapshot_database",
                "database IN ('db1', 'db2', 'db3')",
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    op.execute("DELETE FROM inventory_snapshots WHERE database = 'db3'")
    if "db3" in _check_sql(inspector, "inventory_snapshots", "ck_inventory_snapshot_database"):
        with op.batch_alter_table("inventory_snapshots") as batch:
            batch.drop_constraint("ck_inventory_snapshot_database", type_="check")
            batch.create_check_constraint(
                "ck_inventory_snapshot_database",
                "database IN ('db1', 'db2')",
            )
    with op.batch_alter_table("customer_availability_snapshots") as batch:
        if _pk_columns(inspector, "customer_availability_snapshots") != ["customer_id"]:
            batch.drop_constraint("customer_availability_snapshots_pkey", type_="primary")
            batch.create_primary_key("customer_availability_snapshots_pkey", ["customer_id"])
        if "inventory_database" in _columns(inspector, "customer_availability_snapshots"):
            batch.drop_column("inventory_database")
    if "clean" in _columns(inspector, "lead_requests"):
        with op.batch_alter_table("lead_requests") as batch:
            batch.drop_column("clean")
    op.execute("DELETE FROM lead_database_memberships WHERE inventory_database = 'db3'")
    indexes = _indexes(inspector, "lead_database_memberships")
    if "ix_lead_database_memberships_expires_on" in indexes:
        op.drop_index("ix_lead_database_memberships_expires_on", table_name="lead_database_memberships")
    if "ix_lead_database_memberships_database_state" in indexes:
        op.drop_index("ix_lead_database_memberships_database_state", table_name="lead_database_memberships")
    with op.batch_alter_table("lead_database_memberships") as batch:
        if "ck_lead_database_membership_expiry" in _checks(inspector, "lead_database_memberships"):
            batch.drop_constraint("ck_lead_database_membership_expiry", type_="check")
        if _pk_columns(inspector, "lead_database_memberships") != ["lead_id"]:
            batch.drop_constraint("lead_database_memberships_pkey", type_="primary")
            batch.create_primary_key("lead_database_memberships_pkey", ["lead_id"])
        columns = _columns(inspector, "lead_database_memberships")
        if "expires_on" in columns:
            batch.drop_column("expires_on")
        if "inventory_database" in columns:
            batch.drop_column("inventory_database")
    with op.batch_alter_table("lead_uploads") as batch:
        checks = _checks(inspector, "lead_uploads")
        if "ck_lead_upload_scrubbed_on" in checks:
            batch.drop_constraint("ck_lead_upload_scrubbed_on", type_="check")
        if "ck_lead_upload_inventory_database" in checks:
            batch.drop_constraint("ck_lead_upload_inventory_database", type_="check")
        columns = _columns(inspector, "lead_uploads")
        if "scrubbed_on" in columns:
            batch.drop_column("scrubbed_on")
        if "inventory_database" in columns:
            batch.drop_column("inventory_database")
