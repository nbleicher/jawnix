"""Persist verified public registrations and operator history decisions."""

import sqlalchemy as sa
from alembic import op

revision = "20261015_0050"
down_revision = "20261014_0049"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("customer_registrations"):
        return
    op.create_table(
        "customer_registrations",
        sa.Column("auth_user_id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("first_name", sa.String(120), nullable=False),
        sa.Column("last_name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("licensed_states", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("customer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("agents.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(160)),
        sa.CheckConstraint("status IN ('awaiting_review', 'active', 'rejected')", name="ck_customer_registration_status"),
    )
    op.create_index("ix_customer_registrations_status", "customer_registrations", ["status"])
    op.create_index("ix_customer_registrations_customer_id", "customer_registrations", ["customer_id"])


def downgrade():
    op.drop_table("customer_registrations")
