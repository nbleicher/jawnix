"""Inventory routing and immutable listing reads from the physical DB2."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import BigInteger, Column, Date, MetaData, String, Table, Text, Uuid, and_, case, func, or_, select

from .models import Lead, LeadDatabaseMembership

# Kept outside Base.metadata: PostgreSQL owns this as a read-only foreign
# table, not a second copy of the CSV listing records in DB1.
foreign_metadata = MetaData()
db2_inventory = Table(
    "db2_inventory_rows", foreign_metadata,
    Column("lead_id", BigInteger, primary_key=True),
    Column("upload_id", Uuid, primary_key=True),
    Column("phone", String(10), nullable=False),
    Column("title", Text, nullable=False),
    Column("state", String(2), nullable=False),
    Column("row_number", BigInteger, nullable=False),
    Column("owner_id", Uuid, nullable=False),
    Column("scrubbed_on", Date),
)

# Databases filled by CSV uploads. DB3 rows live in the same physical upload
# store as DB2 rows (the upload's id keys them apart).
UPLOAD_BACKED_DATABASES = ("db2", "db3")
# A Clean row sells for 30 days after its scrub date; expires_on is the first
# day it can no longer sell.
CLEAN_SHELF_DAYS = 30


def inventory_database(request) -> str:
    """A Clean request always draws from DB3; a Standard request from the Customer's Lead Database."""
    if getattr(request, "clean", False):
        return "db3"
    return request.customer.lead_database or "db1"


def inventory_scope(query, database: str):
    query = query.select_from(Lead)
    if database in UPLOAD_BACKED_DATABASES:
        query = query.join(LeadDatabaseMembership, and_(
            LeadDatabaseMembership.lead_id == Lead.id,
            LeadDatabaseMembership.inventory_database == database,
        ))
        if database == "db3":
            query = query.where(LeadDatabaseMembership.expires_on > func.current_date())
        return query
    return query.where(Lead.in_db1.is_(True))


def inventory_state(database: str):
    if database in UPLOAD_BACKED_DATABASES:
        # A global Lead Correction still outranks source listing information.
        return case((Lead.active_correction_id.is_not(None), Lead.state), else_=LeadDatabaseMembership.state)
    return Lead.state


@dataclass(frozen=True)
class SourceListing:
    phone: str
    title: str
    state: str
    provenance: dict
    source_kind: str = "csv_upload"
    segment_key: str = "db2"
    niche: str = ""


def upload_listings(session, source_keys, *, database: str = "db2") -> dict[int, SourceListing]:
    """Only fetch the exact immutable versions selected with the row locks."""
    keys = list(source_keys)
    result = {}
    for offset in range(0, len(keys), 1000):
        grouped = {}
        for lead_id, upload_id in keys[offset:offset + 1000]:
            grouped.setdefault(upload_id, []).append(lead_id)
        rows = session.execute(select(db2_inventory).where(or_(
            *(and_(db2_inventory.c.upload_id == upload_id, db2_inventory.c.lead_id.in_(lead_ids))
              for upload_id, lead_ids in grouped.items())
        ))).mappings()
        for row in rows:
            result[row["lead_id"]] = SourceListing(
                row["phone"], row["title"], row["state"],
                {"kind":"csv_upload", "database":database, "uploadId":str(row["upload_id"]),
                 "ownerId":str(row["owner_id"]), "rowNumber":row["row_number"]},
                segment_key=database,
            )
    return result
