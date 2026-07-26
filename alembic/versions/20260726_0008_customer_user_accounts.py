"""Expand durable Customer identity with replaceable User Accounts."""

import sqlalchemy as sa
from alembic import op


revision = "20260726_0008"
down_revision = "20260725_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    agent_columns = {
        column["name"] for column in inspector.get_columns("agents")
    }
    if "licensed_states" not in agent_columns:
        op.add_column(
            "agents",
            sa.Column(
                "licensed_states",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                sa.text(
                    """
                    UPDATE agents AS customer
                    SET licensed_states = profile.licensed_states
                    FROM customer_profiles AS profile
                    WHERE profile.agent_id = customer.id
                      AND profile.mapping_confirmed_at IS NOT NULL
                      AND profile.user_id = (
                          SELECT candidate.user_id
                          FROM customer_profiles AS candidate
                          WHERE candidate.agent_id = customer.id
                            AND candidate.mapping_confirmed_at IS NOT NULL
                          ORDER BY candidate.updated_at DESC, candidate.user_id
                          LIMIT 1
                      )
                    """
                )
            )
        else:
            op.execute(
                sa.text(
                    """
                    UPDATE agents
                    SET licensed_states = COALESCE(
                        (
                            SELECT customer_profiles.licensed_states
                            FROM customer_profiles
                            WHERE customer_profiles.agent_id = agents.id
                              AND customer_profiles.mapping_confirmed_at IS NOT NULL
                            ORDER BY customer_profiles.updated_at DESC,
                                     customer_profiles.user_id
                            LIMIT 1
                        ),
                        '[]'
                    )
                    """
                )
            )

    inspector = sa.inspect(op.get_bind())
    if "user_accounts" in inspector.get_table_names():
        return
    op.create_table(
        "user_accounts",
        sa.Column("auth_user_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["agents.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("auth_user_id"),
    )
    op.create_index(
        "ix_user_accounts_active",
        "user_accounts",
        ["active"],
    )
    op.create_index(
        "ix_user_accounts_customer_id",
        "user_accounts",
        ["customer_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "uq_active_user_account_per_customer",
            "user_accounts",
            ["customer_id"],
            unique=True,
            postgresql_where=sa.text("active"),
        )
    else:
        op.create_index(
            "uq_active_user_account_per_customer",
            "user_accounts",
            ["customer_id"],
            unique=True,
            sqlite_where=sa.text("active = 1"),
        )
    op.execute(
        sa.text(
            """
            INSERT INTO user_accounts (
                auth_user_id,
                customer_id,
                email,
                active,
                created_at,
                replaced_at
            )
            SELECT user_id,
                   agent_id,
                   email,
                   CASE
                       WHEN agent_id IS NULL THEN true
                       WHEN ROW_NUMBER() OVER (
                           PARTITION BY agent_id
                           ORDER BY mapping_confirmed_at DESC,
                                    updated_at DESC,
                                    user_id
                       ) = 1 THEN true
                       ELSE false
                   END,
                   created_at,
                   CASE
                       WHEN agent_id IS NOT NULL
                        AND ROW_NUMBER() OVER (
                            PARTITION BY agent_id
                            ORDER BY mapping_confirmed_at DESC,
                                     updated_at DESC,
                                     user_id
                        ) > 1 THEN updated_at
                       ELSE NULL
                   END
            FROM customer_profiles
            """
        )
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user_accounts" in inspector.get_table_names():
        op.drop_index(
            "uq_active_user_account_per_customer",
            table_name="user_accounts",
        )
        op.drop_index(
            "ix_user_accounts_customer_id",
            table_name="user_accounts",
        )
        op.drop_index(
            "ix_user_accounts_active",
            table_name="user_accounts",
        )
        op.drop_table("user_accounts")
    if "licensed_states" in {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("agents")
    }:
        op.drop_column("agents", "licensed_states")
