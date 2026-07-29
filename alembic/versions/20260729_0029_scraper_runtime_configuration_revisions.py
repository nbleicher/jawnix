"""Journal immutable Scale runtime configuration revisions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260729_0029"
down_revision = "20260729_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if (
        "scraper_runtime_configuration_revisions"
        not in inspector.get_table_names()
    ):
        op.create_table(
            "scraper_runtime_configuration_revisions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column(
                "before_checksum",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column(
                "after_checksum",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column("configuration", sa.JSON(), nullable=False),
            sa.Column("effects", sa.JSON(), nullable=False),
            sa.Column(
                "enqueue_requested",
                sa.Boolean(),
                nullable=False,
            ),
            sa.Column("actor_user_id", sa.Uuid(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in (
            "after_checksum",
            "actor_user_id",
            "created_at",
        ):
            op.create_index(
                f"ix_scraper_runtime_configuration_revisions_{column}",
                "scraper_runtime_configuration_revisions",
                [column],
            )

    # Base.metadata creates the table before Alembic runs on a fresh database,
    # so install the append-only guard independently of table creation.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION
                    refuse_scraper_runtime_configuration_revision_change()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION
                        'scraper_runtime_configuration_revisions are immutable';
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        op.execute(
            sa.text(
                """
                DROP TRIGGER IF EXISTS
                    scraper_runtime_configuration_revisions_immutable
                ON scraper_runtime_configuration_revisions
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER
                    scraper_runtime_configuration_revisions_immutable
                BEFORE UPDATE OR DELETE
                ON scraper_runtime_configuration_revisions
                FOR EACH ROW EXECUTE FUNCTION
                    refuse_scraper_runtime_configuration_revision_change()
                """
            )
        )
    else:
        for operation in ("UPDATE", "DELETE"):
            trigger_name = (
                "scraper_runtime_configuration_revisions_no_"
                f"{operation.lower()}"
            )
            op.execute(
                sa.text(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS
                        {trigger_name}
                    BEFORE {operation}
                    ON scraper_runtime_configuration_revisions
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'scraper_runtime_configuration_revisions are immutable'
                        );
                    END
                    """
                )
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if (
        "scraper_runtime_configuration_revisions"
        not in inspector.get_table_names()
    ):
        return
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                DROP TRIGGER IF EXISTS
                    scraper_runtime_configuration_revisions_immutable
                ON scraper_runtime_configuration_revisions
                """
            )
        )
        op.execute(
            sa.text(
                """
                DROP FUNCTION IF EXISTS
                    refuse_scraper_runtime_configuration_revision_change()
                """
            )
        )
    else:
        for operation in ("update", "delete"):
            op.execute(
                sa.text(
                    "DROP TRIGGER IF EXISTS "
                    "scraper_runtime_configuration_revisions_no_"
                    f"{operation}"
                )
            )
    for column in (
        "created_at",
        "actor_user_id",
        "after_checksum",
    ):
        op.drop_index(
            f"ix_scraper_runtime_configuration_revisions_{column}",
            table_name="scraper_runtime_configuration_revisions",
        )
    op.drop_table("scraper_runtime_configuration_revisions")
