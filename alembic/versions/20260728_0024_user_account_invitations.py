"""Sequence User Account replacement behind an accepted invitation."""

import sqlalchemy as sa
from alembic import op


revision = "20260728_0024"
down_revision = "20260728_0023"
branch_labels = None
depends_on = None


def _active_account_index_predicate(dialect: str) -> str:
    return "active" if dialect == "postgresql" else "active = 1"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)

    account_columns = {
        column["name"] for column in inspector.get_columns("user_accounts")
    }
    if "replaced_by_auth_user_id" not in account_columns:
        op.add_column(
            "user_accounts",
            sa.Column("replaced_by_auth_user_id", sa.Uuid(), nullable=True),
        )

    # The one-active-account rule is a persistence constraint, not a screen
    # rule. Re-assert it here so an environment that lost the index during an
    # earlier expand/contract step cannot silently keep two active accounts.
    account_indexes = {
        index["name"] for index in inspector.get_indexes("user_accounts")
    }
    if "uq_active_user_account_per_customer" not in account_indexes:
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_active_user_account_per_customer "
                "ON user_accounts (customer_id) "
                f"WHERE {_active_account_index_predicate(dialect)}"
            )
        )

    if "user_account_invitations" in inspector.get_table_names():
        return
    op.create_table(
        "user_account_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("auth_user_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("replaces_auth_user_id", sa.Uuid(), nullable=True),
        sa.Column("invited_by", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'canceled')",
            name="ck_user_account_invitations_status",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["agents.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_account_invitations_customer_id",
        "user_account_invitations",
        ["customer_id"],
    )
    op.create_index(
        "ix_user_account_invitations_auth_user_id",
        "user_account_invitations",
        ["auth_user_id"],
    )
    # Uniqueness applies to outstanding offers, not to invitation history: an
    # identity may be invited again after a cancellation, and a Customer may
    # be invited to again after a replacement completes.
    #
    # One outstanding invitation per Customer is what makes acceptance a safe
    # atomic swap -- the winner of this index is the only account that can
    # ever be promoted.
    where = sa.text("status = 'pending'")
    predicate = (
        {"postgresql_where": where}
        if dialect == "postgresql"
        else {"sqlite_where": where}
    )
    op.create_index(
        "uq_pending_user_account_invitation_per_customer",
        "user_account_invitations",
        ["customer_id"],
        unique=True,
        **predicate,
    )
    op.create_index(
        "uq_pending_user_account_invitation_per_identity",
        "user_account_invitations",
        ["auth_user_id"],
        unique=True,
        **predicate,
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user_account_invitations" in inspector.get_table_names():
        for index in (
            "uq_pending_user_account_invitation_per_identity",
            "uq_pending_user_account_invitation_per_customer",
            "ix_user_account_invitations_auth_user_id",
            "ix_user_account_invitations_customer_id",
        ):
            op.drop_index(index, table_name="user_account_invitations")
        op.drop_table("user_account_invitations")
    if "replaced_by_auth_user_id" in {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("user_accounts")
    }:
        op.drop_column("user_accounts", "replaced_by_auth_user_id")
