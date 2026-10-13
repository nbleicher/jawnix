"""Scoped customer administrators and physical DB2 inventory publication."""
from alembic import op
import sqlalchemy as sa

revision = "20261013_0046"
down_revision = "20261012_0045"
branch_labels = None
depends_on = None


def upgrade():
    from jawnix.models import ManagedAdmin, LeadUpload, LeadDatabaseMembership, LeadDatabaseSummary
    bind = op.get_bind()
    ManagedAdmin.__table__.create(bind, checkfirst=True)
    columns = {row["name"] for row in sa.inspect(bind).get_columns("agents")}
    if "lead_database" not in columns:
        with op.batch_alter_table("agents") as batch:
            batch.add_column(sa.Column("lead_database", sa.String(4), nullable=False, server_default="db1"))
            batch.add_column(sa.Column("allowed_manager_id", sa.Uuid()))
            batch.add_column(sa.Column("manager_id", sa.Uuid()))
            batch.create_foreign_key("fk_customer_allowed_manager", "managed_admins", ["allowed_manager_id"], ["auth_user_id"], ondelete="RESTRICT")
            batch.create_foreign_key("fk_customer_manager", "managed_admins", ["manager_id"], ["auth_user_id"], ondelete="RESTRICT")
            batch.create_index("ix_agents_allowed_manager_id", ["allowed_manager_id"])
            batch.create_index("ix_agents_manager_id", ["manager_id"])
            batch.create_check_constraint("ck_customer_lead_database", "lead_database IN ('db1','db2')")
            batch.create_check_constraint("ck_customer_manager_allowed", "manager_id IS NULL OR (allowed_manager_id IS NOT NULL AND manager_id = allowed_manager_id)")
    if "in_db1" not in {row["name"] for row in sa.inspect(bind).get_columns("lead_inventory")}:
        op.add_column("lead_inventory", sa.Column("in_db1", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "inventory_database" not in {row["name"] for row in sa.inspect(bind).get_columns("distribution_events")}:
        op.add_column("distribution_events", sa.Column("inventory_database", sa.String(4), nullable=False, server_default="db1"))
    for model in (LeadUpload, LeadDatabaseMembership, LeadDatabaseSummary):
        model.__table__.create(bind, checkfirst=True)
    correction_columns = {row["name"] for row in sa.inspect(bind).get_columns("lead_correction_events")}
    correction_checks = sa.inspect(bind).get_check_constraints("lead_correction_events")
    has_csv_kind = any(row["name"] == "ck_lead_correction_based_on_kind" and "csv_upload" in row["sqltext"] for row in correction_checks)
    if "based_on_upload_id" not in correction_columns or not has_csv_kind:
        with op.batch_alter_table("lead_correction_events") as batch:
            if "based_on_upload_id" not in correction_columns:
                batch.add_column(sa.Column("based_on_upload_id", sa.Uuid()))
                batch.create_foreign_key("fk_correction_csv_upload", "lead_uploads", ["based_on_upload_id"], ["id"], ondelete="RESTRICT")
            if not has_csv_kind:
                batch.drop_constraint("ck_lead_correction_based_on_kind", type_="check")
                batch.create_check_constraint("ck_lead_correction_based_on_kind", "based_on_kind IN ('current_listing','legacy_snapshot','prior_correction','csv_upload','none','unknown')")
    if not bind.scalar(sa.select(LeadDatabaseSummary.id).where(LeadDatabaseSummary.id == "db2")):
        bind.execute(LeadDatabaseSummary.__table__.insert().values(id="db2", total=0, states={}, updated_at=sa.func.now()))
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            invalid = bind.scalar(sa.text("SELECT 1 FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid WHERE c.relname='lead_inventory_allocation_age_idx' AND NOT i.indisvalid"))
            if invalid:
                op.execute("DROP INDEX CONCURRENTLY lead_inventory_allocation_age_idx")
            op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS lead_inventory_allocation_age_idx ON lead_inventory (last_distributed_at ASC NULLS FIRST, id ASC)")


def downgrade():
    # Uploads and distribution source identity are durable. Dropping their
    # references would destroy the evidence needed to rebuild delivered files.
    raise RuntimeError("Restore the pre-migration database backup to revert DB2 inventory.")
