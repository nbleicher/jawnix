"""Snapshot fulfillment identity and delivered listing data."""

import sqlalchemy as sa
from alembic import op


revision = "20260725_0003"
down_revision = "20260725_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {
        column["name"]
        for column in inspector.get_columns("distribution_events")
    }
    if "customer_name" in existing_columns:
        return
    op.add_column(
        "distribution_events",
        sa.Column(
            "customer_name",
            sa.String(length=160),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "distribution_events",
        sa.Column(
            "agency_id",
            sa.BigInteger(),
            sa.ForeignKey("agencies.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "distribution_events",
        sa.Column(
            "agency_name",
            sa.String(length=160),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "distribution_events",
        sa.Column("phone", sa.String(length=10), nullable=False, server_default=""),
    )
    op.add_column(
        "distribution_events",
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "distribution_events",
        sa.Column("state", sa.String(length=2), nullable=False, server_default=""),
    )
    op.add_column(
        "distribution_events",
        sa.Column(
            "listing_provenance",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_index(
        "ix_distribution_events_agency_id",
        "distribution_events",
        ["agency_id"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE distribution_events AS event
                SET phone = lead.phone,
                    title = lead.title,
                    state = lead.state,
                    listing_provenance = json_build_object(
                        'kind', 'legacy',
                        'source', lead.source_flow
                    )
                FROM lead_inventory AS lead
                WHERE lead.id = event.lead_id
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE distribution_events AS event
                SET customer_name = customer.name,
                    agency_id = customer.agency_id,
                    agency_name = COALESCE(agency.name, '')
                FROM agents AS customer
                LEFT JOIN agencies AS agency ON agency.id = customer.agency_id
                WHERE customer.id = event.agent_id
                """
            )
        )
    else:
        op.execute(
            sa.text(
                """
                UPDATE distribution_events
                SET customer_name = COALESCE(
                        (SELECT name FROM agents WHERE id = distribution_events.agent_id),
                        ''
                    ),
                    agency_id = (
                        SELECT agency_id
                        FROM agents
                        WHERE id = distribution_events.agent_id
                    ),
                    agency_name = COALESCE(
                        (
                            SELECT agencies.name
                            FROM agents
                            JOIN agencies ON agencies.id = agents.agency_id
                            WHERE agents.id = distribution_events.agent_id
                        ),
                        ''
                    ),
                    phone = (
                        SELECT phone
                        FROM lead_inventory
                        WHERE id = distribution_events.lead_id
                    ),
                    title = (
                        SELECT title
                        FROM lead_inventory
                        WHERE id = distribution_events.lead_id
                    ),
                    state = (
                        SELECT state
                        FROM lead_inventory
                        WHERE id = distribution_events.lead_id
                    ),
                    listing_provenance = json_object(
                        'kind',
                        'legacy',
                        'source',
                        (
                            SELECT source_flow
                            FROM lead_inventory
                            WHERE id = distribution_events.lead_id
                        )
                    )
                """
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_distribution_events_agency_id",
        table_name="distribution_events",
    )
    op.drop_column("distribution_events", "listing_provenance")
    op.drop_column("distribution_events", "state")
    op.drop_column("distribution_events", "title")
    op.drop_column("distribution_events", "phone")
    op.drop_column("distribution_events", "agency_name")
    op.drop_column("distribution_events", "agency_id")
    op.drop_column("distribution_events", "customer_name")
