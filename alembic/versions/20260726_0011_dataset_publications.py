"""Add staged Scrape Run and committed dataset version evidence."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0011"
down_revision = "20260726_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    run_columns = {
        column["name"] for column in inspector.get_columns("scraper_runs")
    }
    if "configuration_id" not in run_columns:
        op.add_column(
            "scraper_runs",
            sa.Column("configuration_id", sa.Uuid(), nullable=True),
        )
        op.add_column(
            "scraper_runs",
            sa.Column("dataset_version", sa.BigInteger(), nullable=True),
        )
        op.add_column(
            "scraper_runs",
            sa.Column(
                "staged_path",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
        )
        op.add_column(
            "scraper_runs",
            sa.Column(
                "manual",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.create_foreign_key(
            "fk_scraper_run_configuration",
            "scraper_runs",
            "scraper_configurations",
            ["configuration_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            "ix_scraper_runs_configuration_id",
            "scraper_runs",
            ["configuration_id"],
        )
        op.create_index(
            "ix_scraper_runs_dataset_version",
            "scraper_runs",
            ["dataset_version"],
        )
    inspector = sa.inspect(op.get_bind())
    if "dataset_publications" in inspector.get_table_names():
        return
    op.create_table(
        "dataset_publications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("scraper_run_id", sa.BigInteger(), nullable=False),
        sa.Column("configuration_id", sa.Uuid(), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["scraper_configurations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scraper_run_id"],
            ["scraper_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checksum"),
        sa.UniqueConstraint("scraper_run_id"),
        sa.UniqueConstraint("version"),
    )
    for column in ("committed_at", "configuration_id", "scraper_run_id"):
        op.create_index(
            f"ix_dataset_publications_{column}",
            "dataset_publications",
            [column],
            unique=column == "scraper_run_id",
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "dataset_publications" in inspector.get_table_names():
        for column in (
            "scraper_run_id",
            "configuration_id",
            "committed_at",
        ):
            op.drop_index(
                f"ix_dataset_publications_{column}",
                table_name="dataset_publications",
            )
        op.drop_table("dataset_publications")
    run_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("scraper_runs")
    }
    if "configuration_id" in run_columns:
        op.drop_index(
            "ix_scraper_runs_dataset_version",
            table_name="scraper_runs",
        )
        op.drop_index(
            "ix_scraper_runs_configuration_id",
            table_name="scraper_runs",
        )
        op.drop_constraint(
            "fk_scraper_run_configuration",
            "scraper_runs",
            type_="foreignkey",
        )
        op.drop_column("scraper_runs", "manual")
        op.drop_column("scraper_runs", "staged_path")
        op.drop_column("scraper_runs", "dataset_version")
        op.drop_column("scraper_runs", "configuration_id")
