"""Operator global search: one box in the header for every record type.

Each group is a small indexed or bounded read with its own result limit, and
groups come back in a fixed order so the dropdown does not jump around as the
operator types. Adding a record type is adding a provider to ``PROVIDERS``.

A query that is only a phone number also finds the Lead, the Batches it was
delivered in, and the DB2 upload it came from; the Lead result opens lead
lookup.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, selectinload

from .batches import batch_href, batch_reference_filter, contains, stage
from .lead_lookup import format_phone, lookup_href, parse_phone
from .models import (
    Customer,
    DistributionEvent,
    EligibilityHold,
    ExclusionList,
    ExclusionPhone,
    Lead,
    LeadDatabaseMembership,
    LeadRequest,
    LeadUpload,
    ManagedAdmin,
    UserAccount,
)

MIN_QUERY_LENGTH = 2
GROUP_LIMIT = 5
UPLOADS_HREF = "/app/admin/inventory/uploads"

_STAGE_LABELS = {
    "awaiting_approval": "Awaiting approval",
    "queued": "Approved",
    "generating": "Generating",
    "waiting": "Waiting",
    "generated": "Generated",
    "delivered": "Delivered",
    "file_expired": "File expired",
    "delivery_failed": "Delivery failed",
    "failed": "Failed",
    "rejected": "Rejected",
    "canceled": "Canceled",
}


def _date(value: datetime | None) -> str:
    return f"{value:%b} {value.day}" if value is not None else ""


def _customers(db: Session, query: str) -> list[dict[str, object]]:
    """Customers by name, slug, or the email of any of their User Accounts."""
    by_email = exists(
        select(UserAccount.auth_user_id).where(
            UserAccount.customer_id == Customer.id,
            contains(UserAccount.email, query),
        )
    )
    customers = list(
        db.scalars(
            select(Customer)
            .where(
                Customer.deleted_at.is_(None),
                or_(contains(Customer.name, query), contains(Customer.slug, query), by_email),
            )
            .order_by(Customer.active.desc(), Customer.name)
            .limit(GROUP_LIMIT)
        )
    )
    emails = {
        account.customer_id: account.email
        for account in db.scalars(
            select(UserAccount).where(
                UserAccount.customer_id.in_({item.id for item in customers}),
                UserAccount.active.is_(True),
            )
        )
    } if customers else {}
    return [
        {
            "kind": "customer",
            "id": str(item.id),
            "title": item.name,
            "detail": " · ".join(
                part
                for part in (
                    item.slug,
                    emails.get(item.id, ""),
                    "" if item.active else "Deactivated",
                )
                if part
            ),
            "href": f"/app/admin/customers/{item.id}",
        }
        for item in customers
    ]


def _batches(db: Session, query: str) -> list[dict[str, object]]:
    """Batches by number ("1042", "#1042") or UUID prefix ("REQ-1a2b3c4d")."""
    reference = batch_reference_filter(query)
    if reference is None:
        return []
    items = db.scalars(
        select(LeadRequest)
        .options(selectinload(LeadRequest.agent), selectinload(LeadRequest.artifact))
        .where(reference)
        .order_by(LeadRequest.number.desc())
        .limit(GROUP_LIMIT)
    )
    return [
        {
            "kind": "batch",
            "id": str(item.id),
            "title": f"Batch #{item.number} · {item.customer.name}",
            "detail": (
                f"{item.lead_count:,} leads · "
                f"{_STAGE_LABELS.get(stage(item), item.status)} · "
                f"{_date(item.created_at)}"
            ),
            "href": batch_href(item),
        }
        for item in items
    ]


def _uploads(db: Session, query: str) -> list[dict[str, object]]:
    """DB2 Lead Uploads by file name, newest first."""
    uploads = list(
        db.scalars(
            select(LeadUpload)
            .where(contains(LeadUpload.filename, query))
            .order_by(LeadUpload.created_at.desc())
            .limit(GROUP_LIMIT)
        )
    )
    managers = {
        manager.auth_user_id: manager.name
        for manager in db.scalars(
            select(ManagedAdmin).where(
                ManagedAdmin.auth_user_id.in_(
                    {item.manager_id for item in uploads if item.manager_id}
                )
            )
        )
    } if any(item.manager_id for item in uploads) else {}
    return [
        {
            "kind": "upload",
            "id": str(item.id),
            "title": item.filename,
            "detail": " · ".join(
                part
                for part in (
                    managers.get(item.manager_id, "Operator")
                    if item.manager_id
                    else "Operator",
                    item.state,
                    _date(item.created_at),
                    item.status.replace("_", " ").capitalize(),
                )
                if part
            ),
            "href": UPLOADS_HREF,
        }
        for item in uploads
    ]


def _phone_lead(db: Session, query: str) -> tuple[str, Lead | None] | None:
    phone = parse_phone(query)
    if phone is None:
        return None
    return phone, db.scalar(select(Lead).where(Lead.phone == phone))


def _leads(db: Session, query: str) -> list[dict[str, object]]:
    """The Lead behind a phone number. Answers every phone-shaped query, so a
    number that is not in the inventory still opens lead lookup."""
    found = _phone_lead(db, query)
    if found is None:
        return []
    phone, lead = found
    if lead is None:
        return [
            {
                "kind": "lead",
                "id": phone,
                "title": format_phone(phone),
                "detail": "Not in DB1 or DB2",
                "href": lookup_href(phone),
            }
        ]
    in_db2 = db.get(LeadDatabaseMembership, lead.id) is not None
    databases = (
        "DB1 and DB2" if lead.in_db1 and in_db2 else "DB2" if in_db2 else "DB1"
    )
    held = db.scalar(
        select(
            exists().where(
                EligibilityHold.lead_id == lead.id, EligibilityHold.active.is_(True)
            )
        )
    )
    excluded = db.scalar(
        select(
            exists().where(
                ExclusionPhone.phone == lead.phone,
                ExclusionPhone.exclusion_list_id == ExclusionList.id,
                ExclusionList.global_effective.is_(True),
            )
        )
    )
    usable = (
        "suppressed"
        if lead.suppressed
        else "on hold"
        if held
        else "excluded"
        if excluded
        else "usable"
    )
    return [
        {
            "kind": "lead",
            "id": str(lead.id),
            "title": " · ".join(
                part for part in (format_phone(lead.phone), lead.title, lead.state) if part
            ),
            "detail": f"{databases} · {usable}",
            "href": lookup_href(lead.phone),
        }
    ]


def _lead_batches(db: Session, query: str) -> list[dict[str, object]]:
    """The Batches a phone number was delivered in, newest first."""
    found = _phone_lead(db, query)
    if found is None or found[1] is None:
        return []
    rows = db.execute(
        select(DistributionEvent, LeadRequest)
        .join(LeadRequest, LeadRequest.id == DistributionEvent.request_id)
        .where(DistributionEvent.lead_id == found[1].id)
        .order_by(DistributionEvent.delivered_at.desc())
        .limit(GROUP_LIMIT)
    ).all()
    return [
        {
            "kind": "batch",
            "id": str(batch.id),
            "title": f"Batch #{batch.number} · {event.customer_name or batch.customer.name}",
            "detail": f"{_date(event.delivered_at)} · {event.inventory_database.upper()}",
            "href": batch_href(batch),
        }
        for event, batch in rows
    ]


def _lead_upload(db: Session, query: str) -> list[dict[str, object]]:
    """The DB2 upload a phone number's DB2 row came from."""
    found = _phone_lead(db, query)
    if found is None or found[1] is None:
        return []
    membership = db.get(LeadDatabaseMembership, found[1].id)
    upload = db.get(LeadUpload, membership.upload_id) if membership is not None else None
    if upload is None:
        return []
    manager = db.get(ManagedAdmin, upload.manager_id) if upload.manager_id else None
    return [
        {
            "kind": "upload",
            "id": str(upload.id),
            "title": upload.filename,
            "detail": " · ".join(
                (manager.name if manager else "Operator", _date(upload.completed_at or upload.created_at))
            ),
            "href": UPLOADS_HREF,
        }
    ]


#: Group key, label, and reader, in display order. The phone-number groups
#: answer only when the query is a phone number, so they lead the list then
#: and are absent otherwise.
PROVIDERS: tuple[tuple[str, str, Callable[[Session, str], list[dict[str, object]]]], ...] = (
    ("lead", "Lead", _leads),
    ("leadBatches", "Delivered in", _lead_batches),
    ("leadUpload", "Uploaded in", _lead_upload),
    ("customers", "Customers", _customers),
    ("batches", "Batches", _batches),
    ("uploads", "DB2 uploads", _uploads),
)


def search(db: Session, query: str) -> dict[str, object]:
    """Grouped matches for the header search box. Empty groups are omitted."""
    text = query.strip()[:120]
    if len(text) < MIN_QUERY_LENGTH:
        return {"query": text, "groups": []}
    groups = []
    for key, label, provider in PROVIDERS:
        results = provider(db, text)
        if results:
            groups.append({"key": key, "label": label, "results": results})
    return {"query": text, "groups": groups}
