"""Let the operator publish CSV uploads into DB2 alongside managed administrators."""

import sqlalchemy as sa
from alembic import op

revision = "20261106_0051"
down_revision = "20261015_0050"
branch_labels = None
depends_on = None

CHECK = "ck_lead_upload_one_uploader"


def upgrade():
    # Revision 0001 builds fresh schemas from current models, so every step is guarded.
    inspector = sa.inspect(op.get_bind())
    columns = {row["name"]: row for row in inspector.get_columns("lead_uploads")}
    checks = {row["name"] for row in inspector.get_check_constraints("lead_uploads")}
    if "operator_id" in columns and columns["manager_id"]["nullable"] and CHECK in checks:
        return
    with op.batch_alter_table("lead_uploads") as batch:
        if "operator_id" not in columns:
            batch.add_column(sa.Column("operator_id", sa.Uuid()))
        if not columns["manager_id"]["nullable"]:
            batch.alter_column("manager_id", existing_type=sa.Uuid(), nullable=True)
        if CHECK not in checks:
            batch.create_check_constraint(CHECK, "(manager_id IS NULL) <> (operator_id IS NULL)")


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT 1 FROM lead_uploads WHERE operator_id IS NOT NULL LIMIT 1")):
        # Published uploads back delivered batches and cannot be dropped.
        raise RuntimeError("Operator uploads exist. Restore the pre-migration database backup to revert.")
    with op.batch_alter_table("lead_uploads") as batch:
        batch.drop_constraint(CHECK, type_="check")
        batch.alter_column("manager_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_column("operator_id")
