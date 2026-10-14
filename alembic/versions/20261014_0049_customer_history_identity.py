"""Keep archived customer names separate from replaceable login profiles."""

import sqlalchemy as sa
from alembic import op

revision = "20261014_0049"
down_revision = "20261014_0048"
branch_labels = None
depends_on = None


def upgrade():
    # The initial migration bootstraps current Base metadata on a fresh DB.
    if sa.inspect(op.get_bind()).has_table("customer_history_identities"):
        return
    op.create_table(
        "customer_history_identities",
        sa.Column("customer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("first_name", sa.String(120), nullable=False),
        sa.Column("last_name", sa.String(120), nullable=False),
    )
    op.create_index("ix_customer_history_identity_name", "customer_history_identities", ["first_name", "last_name"])


def downgrade():
    op.drop_table("customer_history_identities")
