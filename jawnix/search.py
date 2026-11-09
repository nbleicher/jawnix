"""Operator global search: one box in the header for every record type.

Each group is a small indexed or bounded read with its own result limit, and
groups come back in a fixed order so the dropdown does not jump around as the
operator types. Adding a record type is adding a provider to ``PROVIDERS``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, selectinload

from .batches import batch_href, batch_reference_filter, contains, stage
from .models import (
    Customer,
    LeadRequest,
    LeadUpload,
    ManagedAdmin,
    UserAccount,
)

MIN_QUERY_LENGTH = 2
GROUP_LIMIT = 5

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
            "href": "/app/admin/lead-uploads",
        }
        for item in uploads
    ]


#: Group key, label, and reader, in display order. Phone-number Lead results
#: (lead lookup, phase A4) slot in here as one more provider that answers
#: only when the query is a phone number.
PROVIDERS: tuple[tuple[str, str, Callable[[Session, str], list[dict[str, object]]]], ...] = (
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
