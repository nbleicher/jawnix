"""Preserve hierarchy history with soft deletion."""

import sqlalchemy as sa
from alembic import op


revision = "20260725_0002"
down_revision = "20260721_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    agency_columns = {column["name"] for column in inspector.get_columns("agencies")}
    agent_columns = {column["name"] for column in inspector.get_columns("agents")}
    if "deleted_at" not in agency_columns:
        op.add_column("agencies", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    if "deleted_at" not in agent_columns:
        op.add_column("agents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "deleted_at" in {column["name"] for column in inspector.get_columns("agents")}:
        op.drop_column("agents", "deleted_at")
    if "deleted_at" in {column["name"] for column in inspector.get_columns("agencies")}:
        op.drop_column("agencies", "deleted_at")
