"""DB3 expiry: expired Clean memberships leave sale, and DB3-only phones move to DB1."""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .activity import record_activity
from .inventory_snapshots import enqueue_inventory_refresh
from .lead_databases import upload_listings
from .models import Lead, LeadDatabaseMembership

EXPIRE_CLEAN_INVENTORY_JOB = "expire_clean_inventory"
CHUNK = 5000

# D15 open question, built as the plan assumes: when a Clean membership
# expires, a phone whose only other inventory is DB2 stays a DB2 upload lead
# and is NOT added to DB1. Flip to True to promote those phones too.
PROMOTE_DB2_ONLY_LEADS_ON_CLEAN_EXPIRY = False


def _expired_chunk(session: Session, today: date) -> list[LeadDatabaseMembership]:
    return list(session.scalars(
        select(LeadDatabaseMembership)
        .where(
            LeadDatabaseMembership.inventory_database == "db3",
            LeadDatabaseMembership.expires_on <= today,
        )
        .order_by(LeadDatabaseMembership.lead_id)
        .limit(CHUNK)
        .with_for_update(skip_locked=True)
    ))


def expire_clean_inventory(session: Session, settings, *, today: date | None = None) -> dict:
    """One sweep: drop expired DB3 memberships and move DB3-only phones to DB1.

    Title and state for a moved phone come from its DB3 upload row unless a
    Lead Correction already overrides them — the same guard Inventory Sync
    uses when the scraper re-finds a DB2 phone (jawnix_data/migration.py).
    Past DB3 sales are unaffected: delivery history lives on
    ``distribution_events`` and never expires.
    """
    today = today or session.scalar(select(func.current_date()))
    expired = moved = 0
    while chunk := _expired_chunk(session, today):
        leads = {lead.id: lead for lead in session.scalars(
            select(Lead).where(Lead.id.in_([row.lead_id for row in chunk])))}
        in_db2 = set(session.scalars(
            select(LeadDatabaseMembership.lead_id).where(
                LeadDatabaseMembership.inventory_database == "db2",
                LeadDatabaseMembership.lead_id.in_(list(leads)),
            )))
        promotions = [
            lead for lead in leads.values()
            if not lead.in_db1 and (PROMOTE_DB2_ONLY_LEADS_ON_CLEAN_EXPIRY or lead.id not in in_db2)
        ]
        promote_ids = {lead.id for lead in promotions}
        listings = upload_listings(
            session,
            [(row.lead_id, row.upload_id) for row in chunk if row.lead_id in promote_ids],
            database="db3",
        ) if promote_ids else {}
        for lead in promotions:
            lead.in_db1 = True
            if lead.active_correction_id is None:
                listing = listings.get(lead.id)
                if listing is not None:
                    lead.title, lead.state = listing.title, listing.state
        session.execute(delete(LeadDatabaseMembership).where(
            LeadDatabaseMembership.inventory_database == "db3",
            LeadDatabaseMembership.lead_id.in_([row.lead_id for row in chunk]),
        ))
        expired += len(chunk)
        moved += len(promotions)
        session.flush()
    record_activity(
        session,
        action="clean_inventory_expired",
        target_type="lead_database",
        target_id="db3",
        actor_id="system:clean-expiry",
        reason="Expired Clean memberships swept",
        details={"expired": expired, "movedToDb1": moved, "asOf": today.isoformat()},
    )
    enqueue_inventory_refresh(session, reason="db3_expiry")
    return {"expired": expired, "movedToDb1": moved}
