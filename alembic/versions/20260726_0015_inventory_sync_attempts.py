"""Track version-bound, replayable Inventory Sync attempts."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0015"
down_revision = "20260726_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "dataset_publications"
        )
    }
    if "storage_path" not in columns:
        op.add_column(
            "dataset_publications",
            sa.Column(
                "storage_path",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
        )
        op.add_column(
            "dataset_publications",
            sa.Column(
                "sync_status",
                sa.String(length=24),
                nullable=False,
                server_default="pending",
            ),
        )
        op.add_column(
            "dataset_publications",
            sa.Column(
                "synchronized_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_dataset_publications_sync_status",
            "dataset_publications",
            ["sync_status"],
        )
    if (
        "inventory_sync_attempts"
        not in sa.inspect(op.get_bind()).get_table_names()
    ):
        op.create_table(
            "inventory_sync_attempts",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column(
                "dataset_publication_id",
                sa.BigInteger(),
                nullable=False,
            ),
            sa.Column("dataset_version", sa.BigInteger(), nullable=False),
            sa.Column(
                "dataset_checksum",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "finished_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(
                ["dataset_publication_id"],
                ["dataset_publications.id"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "dataset_publication_id",
                "attempt_number",
                name="uq_inventory_sync_publication_attempt",
            ),
        )
        for column in (
            "dataset_publication_id",
            "dataset_version",
            "dataset_checksum",
            "status",
        ):
            op.create_index(
                f"ix_inventory_sync_attempts_{column}",
                "inventory_sync_attempts",
                [column],
            )


def downgrade() -> None:
    if (
        "inventory_sync_attempts"
        in sa.inspect(op.get_bind()).get_table_names()
    ):
        for column in (
            "status",
            "dataset_checksum",
            "dataset_version",
            "dataset_publication_id",
        ):
            op.drop_index(
                f"ix_inventory_sync_attempts_{column}",
                table_name="inventory_sync_attempts",
            )
        op.drop_table("inventory_sync_attempts")
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "dataset_publications"
        )
    }
    if "storage_path" in columns:
        op.drop_index(
            "ix_dataset_publications_sync_status",
            table_name="dataset_publications",
        )
        op.drop_column("dataset_publications", "synchronized_at")
        op.drop_column("dataset_publications", "sync_status")
        op.drop_column("dataset_publications", "storage_path")
