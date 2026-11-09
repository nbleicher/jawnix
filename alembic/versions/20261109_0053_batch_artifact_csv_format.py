"""Record each Batch Artifact's CSV column layout so regeneration stays exact.

New batches add a state column. Every artifact that already exists was written
as phone,title and must keep rebuilding that way, because its checksum is
stored and Customers may already hold the file.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261109_0053"
down_revision = "20261109_0052"
branch_labels = None
depends_on = None

TABLE = "batch_artifacts"
COLUMN = "csv_format"


def upgrade():
    # Revision 0001 builds fresh schemas from current models, so every step is guarded.
    columns = {row["name"] for row in sa.inspect(op.get_bind()).get_columns(TABLE)}
    if COLUMN in columns:
        return
    # The server default stamps every existing artifact, and any written by
    # code that predates this column, with the original two-column layout.
    op.add_column(
        TABLE,
        sa.Column(
            COLUMN,
            sa.String(32),
            server_default="phone_title",
            nullable=False,
        ),
    )


def downgrade():
    bind = op.get_bind()
    columns = {row["name"] for row in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN not in columns:
        return
    if bind.scalar(
        sa.text(f"SELECT 1 FROM {TABLE} WHERE {COLUMN} <> 'phone_title' LIMIT 1")
    ):
        # Without the column those files would regenerate without their state
        # column and no longer match their stored checksums.
        raise RuntimeError(
            "Batch Artifacts with a state column exist. Restore the "
            "pre-migration database backup to revert."
        )
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column(COLUMN)
