"""Clean inventory (DB3): per-database memberships with expiry.

A Lead can now hold one membership per upload-backed database, so a phone can
sell from DB2 and DB3 at once. DB3 memberships carry ``expires_on`` (the scrub
date plus 30 days); DB2 memberships never expire. DB3 uploads record their
scrub date on ``lead_uploads.scrubbed_on``. Batch Requests record whether they
draw Clean (DB3) inventory, and Customer Availability Snapshots key by
database so Standard and Clean availability are cached apart.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20261119_0057"
down_revision = "20261112_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    upload_columns = {row["name"] for row in inspector.get_columns("lead_uploads")}
    with op.batch_alter_table("lead_uploads") as batch:
        if "inventory_database" not in upload_columns:
            batch.add_column(
                sa.Column(
                    "inventory_database",
                    sa.String(length=4),
                    server_default="db2",
                    nullable=False,
                )
            )
        if "scrubbed_on" not in upload_columns:
            batch.add_column(sa.Column("scrubbed_on", sa.Date(), nullable=True))
        batch.create_check_constraint(
            "ck_lead_upload_inventory_database",
            "inventory_database IN ('db2', 'db3')",
        )
        batch.create_check_constraint(
            "ck_lead_upload_scrubbed_on",
            "(inventory_database = 'db3') = (scrubbed_on IS NOT NULL)",
        )

    member_columns = {row["name"] for row in inspector.get_columns("lead_database_memberships")}
    with op.batch_alter_table("lead_database_memberships") as batch:
        if "inventory_database" not in member_columns:
            batch.add_column(
                sa.Column(
                    "inventory_database",
                    sa.String(length=4),
                    server_default="db2",
                    nullable=False,
                )
            )
        if "expires_on" not in member_columns:
            batch.add_column(sa.Column("expires_on", sa.Date(), nullable=True))
        batch.drop_constraint("lead_database_memberships_pkey", type_="primary")
        batch.create_primary_key("lead_database_memberships_pkey", ["inventory_database", "lead_id"])
        batch.create_check_constraint(
            "ck_lead_database_membership_expiry",
            "(inventory_database = 'db3') = (expires_on IS NOT NULL)",
        )
    op.create_index(
        "ix_lead_database_memberships_database_state",
        "lead_database_memberships",
        ["inventory_database", "state"],
    )
    op.create_index(
        "ix_lead_database_memberships_expires_on",
        "lead_database_memberships",
        ["expires_on"],
        postgresql_where=sa.text("inventory_database = 'db3'"),
    )

    request_columns = {row["name"] for row in inspector.get_columns("lead_requests")}
    if "clean" not in request_columns:
        with op.batch_alter_table("lead_requests") as batch:
            batch.add_column(
                sa.Column("clean", sa.Boolean(), server_default=sa.false(), nullable=False)
            )

    snapshot_columns = {row["name"] for row in inspector.get_columns("customer_availability_snapshots")}
    with op.batch_alter_table("customer_availability_snapshots") as batch:
        if "inventory_database" not in snapshot_columns:
            batch.add_column(sa.Column("inventory_database", sa.String(length=4), nullable=True))
    op.execute(
        "UPDATE customer_availability_snapshots s SET inventory_database = a.lead_database "
        "FROM agents a WHERE a.id = s.customer_id AND s.inventory_database IS NULL"
    )
    with op.batch_alter_table("customer_availability_snapshots") as batch:
        batch.alter_column("inventory_database", nullable=False)
        batch.drop_constraint("customer_availability_snapshots_pkey", type_="primary")
        batch.create_primary_key(
            "customer_availability_snapshots_pkey", ["customer_id", "inventory_database"]
        )

    with op.batch_alter_table("inventory_snapshots") as batch:
        batch.drop_constraint("ck_inventory_snapshot_database", type_="check")
        batch.create_check_constraint(
            "ck_inventory_snapshot_database",
            "database IN ('db1', 'db2', 'db3')",
        )


def downgrade() -> None:
    op.execute("DELETE FROM inventory_snapshots WHERE database = 'db3'")
    with op.batch_alter_table("inventory_snapshots") as batch:
        batch.drop_constraint("ck_inventory_snapshot_database", type_="check")
        batch.create_check_constraint(
            "ck_inventory_snapshot_database",
            "database IN ('db1', 'db2')",
        )
    with op.batch_alter_table("customer_availability_snapshots") as batch:
        batch.drop_constraint("customer_availability_snapshots_pkey", type_="primary")
        batch.create_primary_key("customer_availability_snapshots_pkey", ["customer_id"])
        batch.drop_column("inventory_database")
    with op.batch_alter_table("lead_requests") as batch:
        batch.drop_column("clean")
    op.execute("DELETE FROM lead_database_memberships WHERE inventory_database = 'db3'")
    op.drop_index("ix_lead_database_memberships_expires_on", table_name="lead_database_memberships")
    op.drop_index("ix_lead_database_memberships_database_state", table_name="lead_database_memberships")
    with op.batch_alter_table("lead_database_memberships") as batch:
        batch.drop_constraint("ck_lead_database_membership_expiry", type_="check")
        batch.drop_constraint("lead_database_memberships_pkey", type_="primary")
        batch.create_primary_key("lead_database_memberships_pkey", ["lead_id"])
        batch.drop_column("expires_on")
        batch.drop_column("inventory_database")
    with op.batch_alter_table("lead_uploads") as batch:
        batch.drop_constraint("ck_lead_upload_scrubbed_on", type_="check")
        batch.drop_constraint("ck_lead_upload_inventory_database", type_="check")
        batch.drop_column("scrubbed_on")
        batch.drop_column("inventory_database")
