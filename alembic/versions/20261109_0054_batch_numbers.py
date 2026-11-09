"""Give every Batch Request a sequential, immutable Batch number.

Existing Batch Requests are numbered in submission order from 1001, so the
oldest batch is #1001. New Batch Requests draw the next number from a
PostgreSQL sequence, which two concurrent submissions can never share.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261109_0054"
down_revision = "20261109_0053"
branch_labels = None
depends_on = None

TABLE = "lead_requests"
COLUMN = "number"
INDEX = "uq_lead_request_number"
SEQUENCE = "lead_requests_number_seq"
START = 1001


def _creation_order(bind) -> list:
    return list(
        bind.execute(
            sa.text(f"SELECT id FROM {TABLE} ORDER BY created_at, id")
        ).scalars()
    )


def upgrade():
    # Revision 0001 builds fresh schemas from current models, so every step is guarded.
    bind = op.get_bind()
    postgresql = bind.dialect.name == "postgresql"
    columns = {row["name"] for row in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN in columns:
        return
    if postgresql:
        # A Batch Request submitted while this runs would miss its number.
        op.execute(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE")
    op.add_column(TABLE, sa.Column(COLUMN, sa.Integer(), nullable=True))
    ids = _creation_order(bind)
    if ids:
        bind.execute(
            sa.text(f"UPDATE {TABLE} SET {COLUMN} = :number WHERE id = :id"),
            [{"number": START + index, "id": id_} for index, id_ in enumerate(ids)],
        )
    if postgresql:
        op.execute(f"CREATE SEQUENCE IF NOT EXISTS {SEQUENCE} START WITH {START}")
        if ids:
            # The next submission continues after the last backfilled number.
            op.execute(sa.text(f"SELECT setval('{SEQUENCE}', {START + len(ids) - 1})"))
    with op.batch_alter_table(TABLE) as batch:
        batch.alter_column(COLUMN, existing_type=sa.Integer(), nullable=False)
        batch.create_index(INDEX, [COLUMN], unique=True)


def downgrade():
    bind = op.get_bind()
    columns = {row["name"] for row in sa.inspect(bind).get_columns(TABLE)}
    if COLUMN not in columns:
        return
    numbers = list(
        bind.execute(
            sa.text(f"SELECT {COLUMN} FROM {TABLE} ORDER BY created_at, id")
        ).scalars()
    )
    if numbers != list(range(START, START + len(numbers))):
        # Customers and Telegram already name these batches by number, and a
        # later upgrade would renumber them in creation order without the gaps.
        raise RuntimeError(
            "Batch numbers no longer match submission order. Restore the "
            "pre-migration database backup to revert."
        )
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_index(INDEX)
        batch.drop_column(COLUMN)
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP SEQUENCE IF EXISTS {SEQUENCE}")
