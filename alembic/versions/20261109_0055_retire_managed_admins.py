"""Retire managed administration.

Every managed administrator becomes inactive, and Customers lose their
managed-administrator assignment columns. The managed_admins table and
lead_uploads.manager_id stay, so earlier DB2 uploads still name their
uploader.

Code that no longer reads the assignment columns must be running on every
service before this runs: older code selects them on every Customer read.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261109_0055"
down_revision = "20261109_0054"
branch_labels = None
depends_on = None

TABLE = "agents"
COLUMNS = ("allowed_manager_id", "manager_id")
CHECK = "ck_customer_manager_allowed"
CHECK_SQL = "manager_id IS NULL OR (allowed_manager_id IS NOT NULL AND manager_id = allowed_manager_id)"
# The names revision 0046 gave them. A schema built from the models of that
# time instead has the database's default foreign key names.
FOREIGN_KEYS = {"allowed_manager_id": "fk_customer_allowed_manager", "manager_id": "fk_customer_manager"}
managed_admins = sa.table("managed_admins", sa.column("active", sa.Boolean()))


def upgrade():
    # Revision 0001 builds fresh schemas from current models, so every step is guarded.
    bind = op.get_bind()
    op.execute(managed_admins.update().where(managed_admins.c.active.is_(True)).values(active=False))
    inspector = sa.inspect(bind)
    present = {row["name"] for row in inspector.get_columns(TABLE)} & set(COLUMNS)
    if not present:
        return
    foreign_keys = [
        row["name"] for row in inspector.get_foreign_keys(TABLE)
        if row["name"] and set(row["constrained_columns"]) & present
    ]
    indexes = [
        row["name"] for row in inspector.get_indexes(TABLE)
        if set(row["column_names"]) & present
    ]
    checks = {row["name"] for row in inspector.get_check_constraints(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        if CHECK in checks:
            batch.drop_constraint(CHECK, type_="check")
        for name in foreign_keys:
            batch.drop_constraint(name, type_="foreignkey")
        for name in indexes:
            batch.drop_index(name)
        for column in COLUMNS:
            if column in present:
                batch.drop_column(column)


def downgrade():
    # The columns come back empty. Managed administrators stay inactive:
    # which of them were active before is not recorded anywhere.
    inspector = sa.inspect(op.get_bind())
    columns = {row["name"] for row in inspector.get_columns(TABLE)}
    if set(COLUMNS) <= columns:
        return
    checks = {row["name"] for row in inspector.get_check_constraints(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        for column in COLUMNS:
            if column in columns:
                continue
            batch.add_column(sa.Column(column, sa.Uuid(), nullable=True))
            batch.create_foreign_key(FOREIGN_KEYS[column], "managed_admins", [column], ["auth_user_id"], ondelete="RESTRICT")
            batch.create_index(f"ix_{TABLE}_{column}", [column])
        if CHECK not in checks:
            batch.create_check_constraint(CHECK, CHECK_SQL)
