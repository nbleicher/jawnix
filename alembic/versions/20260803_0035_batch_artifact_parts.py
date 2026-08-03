"""Freeze Batch Request rows-per-file and describe artifact zip parts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0035"
down_revision = "20260803_0034"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    if "rows_per_file" not in _columns("lead_requests"):
        op.add_column(
            "lead_requests",
            sa.Column("rows_per_file", sa.Integer(), nullable=True),
        )
        op.execute(
            "UPDATE lead_requests SET rows_per_file = lead_count "
            "WHERE rows_per_file IS NULL"
        )
        with op.batch_alter_table("lead_requests") as batch:
            batch.alter_column(
                "rows_per_file",
                existing_type=sa.Integer(),
                nullable=False,
            )

    if "parts" not in _columns("batch_artifacts"):
        op.add_column(
            "batch_artifacts",
            sa.Column(
                "parts",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
        artifacts = sa.table(
            "batch_artifacts",
            sa.column("id", sa.Integer()),
            sa.column("filename", sa.String()),
            sa.column("row_count", sa.Integer()),
            sa.column("parts", sa.JSON()),
        )
        bind = op.get_bind()
        for artifact in bind.execute(
            sa.select(
                artifacts.c.id,
                artifacts.c.filename,
                artifacts.c.row_count,
            )
        ).mappings():
            bind.execute(
                artifacts.update()
                .where(artifacts.c.id == artifact["id"])
                .values(
                    parts=[
                        {
                            "filename": artifact["filename"],
                            "row_count": artifact["row_count"],
                        }
                    ]
                )
            )
        with op.batch_alter_table("batch_artifacts") as batch:
            batch.alter_column(
                "parts",
                existing_type=sa.JSON(),
                server_default=None,
            )


def downgrade() -> None:
    if "parts" in _columns("batch_artifacts"):
        op.drop_column("batch_artifacts", "parts")
    if "rows_per_file" in _columns("lead_requests"):
        op.drop_column("lead_requests", "rows_per_file")
