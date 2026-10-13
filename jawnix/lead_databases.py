"""Inventory routing and immutable listing reads from the physical DB2."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import BigInteger, Column, MetaData, String, Table, Text, Uuid, and_, case, or_, select

from .models import Lead, LeadDatabaseMembership

# Kept outside Base.metadata: PostgreSQL owns this as a read-only foreign
# table, not a second copy of Alex's listing records in DB1.
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
)


def inventory_database(customer) -> str:
    return customer.lead_database or "db1"


def inventory_scope(query, database: str):
    query = query.select_from(Lead)
    if database == "db2":
        return query.join(LeadDatabaseMembership, LeadDatabaseMembership.lead_id == Lead.id)
    return query.where(Lead.in_db1.is_(True))


def inventory_state(database: str):
    if database == "db2":
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


def db2_listings(session, source_keys) -> dict[int, SourceListing]:
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
                {"kind":"csv_upload", "database":"db2", "uploadId":str(row["upload_id"]),
                 "ownerId":str(row["owner_id"]), "rowNumber":row["row_number"]},
            )
    return result
