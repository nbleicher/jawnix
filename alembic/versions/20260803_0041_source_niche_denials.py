"""Record denied Source Niche proposals for the admin review queue."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0041"
down_revision = "20260803_0040"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    columns = _columns("source_niche_mappings")
    if "denied_by" not in columns:
        op.add_column(
            "source_niche_mappings",
            sa.Column(
                "denied_by",
                sa.String(length=160),
                nullable=False,
                server_default="",
            ),
        )
    if "denied_at" not in columns:
        op.add_column(
            "source_niche_mappings",
            sa.Column("denied_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("source_niche_mappings", "denied_at")
    op.drop_column("source_niche_mappings", "denied_by")
