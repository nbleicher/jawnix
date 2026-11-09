"""The operator Inventory page: usable DB1 and DB2 leads and the tools beside them.

Every figure over the inventory itself comes from the background snapshots in
``inventory_snapshots``; this module only reads them and adds small, indexed
reads (DB2 uploads, Exclusion Lists, suppressed Leads, recent Inventory Syncs).
Nothing here counts the nine-million-row inventory in a request.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .eligibility import RESTORE_LEAD, RESTORE_NOTICE
from .inventory_snapshots import read_inventory_snapshots
from .lead_lookup import lookup_href, uploader_name, uploader_names
from .models import (
    Customer,
    ExclusionList,
    InventorySyncAttempt,
    Lead,
    LeadDatabaseMembership,
    LeadUpload,
    utcnow,
)

PAGE_SIZE = 25
ADDED_WINDOW_DAYS = 7
UPLOADS_IN_PROGRESS = frozenset({"uploading", "queued", "processing"})


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _added_last_week(db: Session, now: datetime) -> int:
    """New DB1 Leads from Inventory Syncs that finished in the last 7 days.

    Each sync records how many Leads it created (``imported``), so this sums a
    week of sync results instead of counting the inventory by first-seen time.
    """
    since = now - timedelta(days=ADDED_WINDOW_DAYS)
    total = 0
    for result in db.scalars(
        select(InventorySyncAttempt.result).where(
            InventorySyncAttempt.status == "complete",
            InventorySyncAttempt.finished_at >= since,
        )
    ):
        imported = (result or {}).get("imported")
        if isinstance(imported, int):
            total += imported
    return total


def _last_upload(db: Session) -> dict[str, object] | None:
    upload = db.scalar(
        select(LeadUpload)
        .where(LeadUpload.status == "complete")
        .order_by(LeadUpload.completed_at.desc().nulls_last(), LeadUpload.created_at.desc())
        .limit(1)
    )
    if upload is None:
        return None
    names = uploader_names(db, [upload])
    return {
        "id": str(upload.id),
        "filename": upload.filename,
        "uploadedBy": uploader_name(names, upload),
        "at": _utc(upload.completed_at or upload.created_at),
    }


def inventory_overview(db: Session, *, now: datetime | None = None) -> dict[str, object]:
    """The Inventory page in one read."""
    now = now or utcnow()
    upload_counts = dict(
        db.execute(select(LeadUpload.status, func.count()).group_by(LeadUpload.status)).all()
    )
    return {
        **read_inventory_snapshots(db),
        "addedLast7Days": _added_last_week(db, now),
        "lastUpload": _last_upload(db),
        "sections": {
            "uploads": int(sum(upload_counts.values())),
            "uploadsInProgress": int(
                sum(count for status, count in upload_counts.items() if status in UPLOADS_IN_PROGRESS)
            ),
            "exclusionsWaiting": int(
                db.scalar(
                    select(func.count())
                    .select_from(ExclusionList)
                    .where(ExclusionList.status == "pending_confirmation")
                )
                or 0
            ),
            # Served by the index on lead_inventory.suppressed: the read is
            # bounded by the suppressed Leads, not the inventory.
            "suppressed": int(
                db.scalar(
                    select(func.count()).select_from(Lead).where(Lead.suppressed.is_(True))
                )
                or 0
            ),
        },
    }


def usable_by_state(db: Session) -> list[dict[str, object]]:
    """Usable Leads per state in both databases, largest total first."""
    snapshots = read_inventory_snapshots(db)
    db1 = (snapshots["db1"] or {}).get("usableByState", {})
    db2 = (snapshots["db2"] or {}).get("usableByState", {})
    rows = [
        {
            "state": state,
            "db1": int(db1.get(state, 0)),
            "db2": int(db2.get(state, 0)),
            "total": int(db1.get(state, 0)) + int(db2.get(state, 0)),
        }
        for state in set(db1) | set(db2)
    ]
    rows.sort(key=lambda row: (-row["total"], row["state"]))
    return rows


def usable_by_state_csv(db: Session) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(["state", "db1_usable", "db2_usable", "total_usable"])
    for row in usable_by_state(db):
        writer.writerow([row["state"], row["db1"], row["db2"], row["total"]])
    return output.getvalue()


def _page_bounds(page: int, page_size: int, total: int) -> tuple[int, int]:
    pages = max(1, math.ceil(total / page_size))
    page = min(max(1, page), pages)
    return page, pages


def _exclusion_row(item: ExclusionList, customers: dict[int, Customer]) -> dict[str, object]:
    customer = customers.get(item.customer_id) if item.customer_id is not None else None
    return {
        "id": str(item.id),
        "customer": (
            {
                "id": customer.id,
                "name": customer.name,
                "href": f"/app/admin/customers/{customer.id}",
            }
            if customer is not None
            else None
        ),
        # The decision panel and the table both name the uploader this way.
        "customerName": customer.name if customer is not None else "Operator",
        "type": item.exclusion_type,
        "filename": item.filename,
        "status": item.status,
        "global": item.global_effective,
        "acceptedRows": item.accepted_rows,
        "invalidRows": item.invalid_rows,
        "duplicateRows": item.duplicate_rows,
        "poolImpact": item.pool_impact,
        "error": item.error,
        "decisionReason": item.decision_reason,
        "createdAt": _utc(item.created_at),
        "decidedAt": _utc(item.decided_at),
    }


def exclusion_lists_overview(
    db: Session, *, page: int = 1, page_size: int = PAGE_SIZE
) -> dict[str, object]:
    """Customer lists waiting for a global decision, then every list, newest first."""
    waiting = list(
        db.scalars(
            select(ExclusionList)
            .where(ExclusionList.status == "pending_confirmation")
            .order_by(ExclusionList.created_at.asc(), ExclusionList.id)
        )
    )
    total = int(db.scalar(select(func.count()).select_from(ExclusionList)) or 0)
    page, pages = _page_bounds(page, page_size, total)
    listed = list(
        db.scalars(
            select(ExclusionList)
            .order_by(ExclusionList.created_at.desc(), ExclusionList.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    customer_ids = {
        item.customer_id for item in [*waiting, *listed] if item.customer_id is not None
    }
    customers = (
        {
            customer.id: customer
            for customer in db.scalars(select(Customer).where(Customer.id.in_(customer_ids)))
        }
        if customer_ids
        else {}
    )
    return {
        "waiting": [_exclusion_row(item, customers) for item in waiting],
        "lists": {
            "items": [_exclusion_row(item, customers) for item in listed],
            "page": page,
            "pageSize": page_size,
            "total": total,
            "pages": pages,
        },
    }


def suppressed_leads_page(
    db: Session, *, page: int = 1, page_size: int = PAGE_SIZE
) -> dict[str, object]:
    """Suppressed Leads, most recently added to the inventory first."""
    total = int(
        db.scalar(select(func.count()).select_from(Lead).where(Lead.suppressed.is_(True))) or 0
    )
    page, pages = _page_bounds(page, page_size, total)
    rows = db.execute(
        select(Lead, LeadDatabaseMembership.state)
        .outerjoin(LeadDatabaseMembership, LeadDatabaseMembership.lead_id == Lead.id)
        .where(Lead.suppressed.is_(True))
        .order_by(Lead.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": lead.id,
                "phone": lead.phone,
                "title": lead.title,
                # A DB2-only Lead keeps its state on the DB2 membership.
                "state": lead.state or db2_state or "",
                "reason": lead.suppression_reason,
                "href": lookup_href(lead.phone),
                "actions": [RESTORE_LEAD.as_dict()],
            }
            for lead, db2_state in rows
        ],
        "restoreNotice": RESTORE_NOTICE,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "pages": pages,
    }
