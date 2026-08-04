"""Admit mixed Exclusion Lists: one file holding landline, DNC, and TCPA rows."""

from __future__ import annotations

from alembic import op


revision = "20260804_0043"
down_revision = "20260803_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("exclusion_lists") as batch:
        batch.drop_constraint(
            "ck_exclusion_lists_type",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_exclusion_lists_type",
            "exclusion_type IN ('mixed', 'landline', 'dnc', 'tcpa_litigator')",
        )


def downgrade() -> None:
    op.execute(
        "UPDATE exclusion_lists SET exclusion_type = 'dnc' "
        "WHERE exclusion_type = 'mixed'"
    )
    with op.batch_alter_table("exclusion_lists") as batch:
        batch.drop_constraint(
            "ck_exclusion_lists_type",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_exclusion_lists_type",
            "exclusion_type IN ('landline', 'dnc', 'tcpa_litigator')",
        )
