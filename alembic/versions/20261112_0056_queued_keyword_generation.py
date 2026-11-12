"""Keyword generation drafts are queued for the keyword worker.

A draft starts as "generating" while the keyword worker owns the provider
call, and lands on "failed" (with failure_detail) when that call does not
produce a reviewable draft. Revision 0001 builds fresh schemas from the
current models, so every step here is guarded.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20261112_0056"
down_revision = "20261109_0055"
branch_labels = None
depends_on = None

TABLE = "keyword_generation_drafts"
CHECK = "ck_keyword_generation_drafts_acceptance_status"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {row["name"] for row in inspector.get_columns(TABLE)}
    if "failure_detail" not in columns:
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(
                sa.Column("failure_detail", sa.String(length=300), nullable=True)
            )
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CHECK, type_="check")
        batch.create_check_constraint(
            CHECK,
            "acceptance_status IN ('generating', 'pending', 'accepted', 'failed')",
        )


def downgrade() -> None:
    op.execute(
        f"UPDATE {TABLE} SET acceptance_status = 'pending' "
        "WHERE acceptance_status IN ('generating', 'failed')"
    )
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CHECK, type_="check")
        batch.create_check_constraint(
            CHECK,
            "acceptance_status IN ('pending', 'accepted')",
        )
    inspector = sa.inspect(op.get_bind())
    columns = {row["name"] for row in inspector.get_columns(TABLE)}
    if "failure_detail" in columns:
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column("failure_detail")
