"""Store usable DB1 and DB2 inventory totals written by a background job."""

import sqlalchemy as sa
from alembic import op

revision = "20261109_0052"
down_revision = "20261106_0051"
branch_labels = None
depends_on = None

TABLE = "inventory_snapshots"


def upgrade():
    # Revision 0001 builds fresh schemas from current models, so every step is guarded.
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("database", sa.String(4), primary_key=True),
        sa.Column("all_rows", sa.BigInteger(), nullable=False),
        sa.Column("suppressed", sa.BigInteger(), nullable=False),
        sa.Column("held", sa.BigInteger(), nullable=False),
        sa.Column("excluded", sa.BigInteger(), nullable=False),
        sa.Column("usable", sa.BigInteger(), nullable=False),
        sa.Column("usable_by_state", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "database IN ('db1', 'db2')",
            name="ck_inventory_snapshot_database",
        ),
    )


def downgrade():
    # A derived cache: the next refresh job rebuilds it from inventory.
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)
