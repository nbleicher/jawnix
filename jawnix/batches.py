"""The operator's Batch views: All batches, one Batch page, and Batch rows.

A Batch is a Batch Request named by its number ("Batch #1042"). These reads
sit beside ``fulfillment`` rather than inside it: Fulfillment owns which
actions a Batch Request offers, and this module only arranges what the
operator reads around them — the stage badge, whether it can be filled, what
it costs, and how it got here. Every figure is a small indexed read; nothing
here counts inventory.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, case, cast, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .billing import available_balance_cents
from .fulfillment import OPEN_STATUSES, describe_request
from .models import (
    BatchArtifact,
    Customer,
    CustomerAvailabilitySnapshot,
    DistributionEvent,
    InventoryConflict,
    LeadReport,
    LeadRequest,
    RequestStatus,
)

PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

#: Status filters on All batches. "expired" is a delivered batch whose file
#: has passed its 30-day expiry and can be regenerated.
STATUS_FILTERS: dict[str, frozenset[str] | None] = {
    "open": OPEN_STATUSES,
    "awaiting_approval": frozenset({RequestStatus.pending.value}),
    "waiting": frozenset({RequestStatus.waiting_inventory.value}),
    "in_progress": frozenset(
        {
            RequestStatus.approved.value,
            RequestStatus.processing.value,
            RequestStatus.generated.value,
        }
    ),
    "failed": frozenset({RequestStatus.failed.value}),
    "delivered": frozenset({RequestStatus.delivered.value}),
    "expired": frozenset({RequestStatus.delivered.value}),
    "closed": frozenset(
        {RequestStatus.rejected.value, RequestStatus.canceled.value}
    ),
    "all": None,
}

#: Batches still before allocation, where the order they wait in matters.
_UNALLOCATED = frozenset(
    {
        RequestStatus.pending.value,
        RequestStatus.approved.value,
        RequestStatus.waiting_inventory.value,
    }
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def batch_href(item: LeadRequest) -> str:
    return f"/app/admin/batches/{item.number}"


def stage(item: LeadRequest, *, now: datetime | None = None) -> str:
    """One word for where a Batch is, including what its status alone hides.

    A failed Batch with a file failed delivery rather than generation, and a
    delivered Batch whose file expired can only be regenerated.
    """
    artifact = item.artifact
    if item.status == RequestStatus.failed.value:
        return "delivery_failed" if artifact is not None else "failed"
    if item.status == RequestStatus.delivered.value:
        expires_at = _utc(artifact.expires_at) if artifact is not None else None
        if expires_at is not None and expires_at <= (now or datetime.now(timezone.utc)):
            return "file_expired"
        return "delivered"
    return {
        RequestStatus.pending.value: "awaiting_approval",
        RequestStatus.approved.value: "queued",
        RequestStatus.processing.value: "generating",
        RequestStatus.waiting_inventory.value: "waiting",
        RequestStatus.generated.value: "generated",
        RequestStatus.rejected.value: "rejected",
        RequestStatus.canceled.value: "canceled",
    }.get(item.status, item.status)


def _database_expression():
    """The Lead Database a Batch drew from, or will draw from.

    Committed Distribution Events record their database; before allocation
    the Customer's current selection decides. One index probe per Batch.
    """
    drawn = (
        select(DistributionEvent.inventory_database)
        .where(DistributionEvent.request_id == LeadRequest.id)
        .limit(1)
        .correlate(LeadRequest)
        .scalar_subquery()
    )
    return func.coalesce(drawn, Customer.lead_database)


def _committed_counts(db: Session, items: list[LeadRequest]) -> dict:
    """Committed Distribution Events, read only where no artifact says so.

    A Batch Artifact's row count is the committed quantity, so counting the
    events of a delivered 50,000-lead batch would be wasted work.
    """
    ids = [
        item.id
        for item in items
        if item.artifact is None and item.status == RequestStatus.processing.value
    ]
    if not ids:
        return {}
    return dict(
        db.execute(
            select(DistributionEvent.request_id, func.count())
            .where(DistributionEvent.request_id.in_(ids))
            .group_by(DistributionEvent.request_id)
        ).all()
    )


def _availability_snapshots(db: Session, items: list[LeadRequest]) -> dict:
    customer_ids = {
        item.agent_id for item in items if item.status in _UNALLOCATED
    }
    if not customer_ids:
        return {}
    return {
        row.customer_id: row
        for row in db.scalars(
            select(CustomerAvailabilitySnapshot).where(
                CustomerAvailabilitySnapshot.customer_id.in_(customer_ids)
            )
        )
    }


def availability(
    item: LeadRequest,
    *,
    committed: int | None,
    snapshot: CustomerAvailabilitySnapshot | None,
) -> dict[str, object]:
    """Can this Batch be filled, from what is already known.

    Committed Batches report what they hold. Before allocation the last
    attempt's count is the most specific figure; a Batch never attempted
    falls back to the Customer's background availability snapshot, which
    covers all of the Customer's states and says when it was counted.
    """
    if item.artifact is not None:
        return {"kind": "committed", "committed": item.artifact.row_count}
    if committed:
        return {"kind": "committed", "committed": int(committed)}
    if item.status in {RequestStatus.rejected.value, RequestStatus.canceled.value}:
        return {"kind": "none"}
    if item.available_count is not None:
        available, as_of, source = item.available_count, None, "last_attempt"
    elif snapshot is not None and item.status in _UNALLOCATED:
        available, as_of, source = (
            snapshot.available,
            _utc(snapshot.computed_at),
            "customer_snapshot",
        )
    else:
        return {"kind": "unknown"}
    short = max(0, item.lead_count - int(available))
    return {
        "kind": "short" if short else "fillable",
        "available": int(available),
        "short": short,
        "asOf": as_of,
        "source": source,
    }


def _pending_conflict_ids(db: Session, items: list[LeadRequest]) -> set:
    ids = [item.id for item in items]
    if not ids:
        return set()
    involved = set()
    for older, newer in db.execute(
        select(
            InventoryConflict.older_request_id,
            InventoryConflict.newer_request_id,
        ).where(
            InventoryConflict.status == "pending",
            or_(
                InventoryConflict.older_request_id.in_(ids),
                InventoryConflict.newer_request_id.in_(ids),
            ),
        )
    ):
        involved.update({older, newer})
    return involved


def _needs_decision(stage_name: str, in_conflict: bool) -> bool:
    return in_conflict or stage_name in {
        "awaiting_approval",
        "delivery_failed",
        "failed",
    }


def batch_rows(
    db: Session,
    items: list[LeadRequest],
    *,
    databases: dict | None = None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Table rows for Batches, with the reads they need done in bulk."""
    moment = now or datetime.now(timezone.utc)
    committed = _committed_counts(db, items)
    snapshots = _availability_snapshots(db, items)
    conflicts = _pending_conflict_ids(db, items)
    rows = []
    for item in items:
        stage_name = stage(item, now=moment)
        rows.append(
            {
                "id": str(item.id),
                "number": item.number,
                "href": batch_href(item),
                "customer": {
                    "id": item.agent_id,
                    "name": item.customer.name,
                    "href": f"/app/admin/customers/{item.agent_id}",
                },
                "leadCount": item.lead_count,
                "states": item.states_snapshot,
                "database": (
                    databases.get(item.id) if databases is not None else None
                )
                or item.customer.lead_database
                or "db1",
                "status": item.status,
                "stage": stage_name,
                "statusMessage": item.status_message,
                "availability": availability(
                    item,
                    committed=committed.get(item.id),
                    snapshot=snapshots.get(item.agent_id),
                ),
                "needsDecision": _needs_decision(stage_name, item.id in conflicts),
                "createdAt": item.created_at,
                "deliveredAt": item.delivered_at,
                "closedAt": item.closed_at,
            }
        )
    return rows


def _row_options():
    return (
        selectinload(LeadRequest.artifact),
        selectinload(LeadRequest.agent),
    )


def batches_with_databases(
    db: Session, statement
) -> tuple[list[LeadRequest], dict]:
    """Run a Batch query that also selects each Batch's database."""
    database = _database_expression().label("database")
    rows = db.execute(
        statement.add_columns(database).options(*_row_options())
    ).all()
    return [row[0] for row in rows], {row[0].id: row.database for row in rows}


_NUMBER = re.compile(r"^(?:batch\s*)?#?\s*(\d{1,9})$", re.IGNORECASE)
_UUID_PREFIX = re.compile(r"^(?:req-)?([0-9a-f-]{4,36})$", re.IGNORECASE)


def batch_reference_filter(query: str):
    """Match a Batch by its number or by the start of its UUID.

    Customers have seen "REQ-" and the first eight UUID characters, so that
    form still finds the Batch. PostgreSQL renders UUIDs with dashes and SQLite
    stores bare hex, so both sides are compared without dashes.
    """
    text = query.strip()
    clauses = []
    number = _NUMBER.match(text)
    if number:
        clauses.append(LeadRequest.number == int(number.group(1)))
    prefix = _UUID_PREFIX.match(text)
    if prefix:
        hex_prefix = prefix.group(1).replace("-", "").lower()
        if len(hex_prefix) >= 4 and re.fullmatch(r"[0-9a-f]+", hex_prefix):
            clauses.append(
                func.replace(func.lower(cast(LeadRequest.id, String)), "-", "")
                .startswith(hex_prefix, autoescape=True)
            )
    return or_(*clauses) if clauses else None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contains(column, query: str):
    return column.ilike(f"%{_escape_like(query.strip())}%", escape="\\")


def list_batches(
    db: Session,
    *,
    q: str = "",
    status: str = "open",
    database: str = "",
    needs_decision: bool = False,
    page: int = 1,
    page_size: int = PAGE_SIZE,
    now: datetime | None = None,
) -> dict[str, object]:
    """All batches, open first and oldest open first: the order they wait in."""
    moment = now or datetime.now(timezone.utc)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    statement = select(LeadRequest).join(Customer, Customer.id == LeadRequest.agent_id)
    statuses = STATUS_FILTERS.get(status, OPEN_STATUSES)
    if statuses is not None:
        statement = statement.where(LeadRequest.status.in_(statuses))
    if status == "expired":
        statement = statement.where(
            exists(
                select(BatchArtifact.id).where(
                    BatchArtifact.request_id == LeadRequest.id,
                    BatchArtifact.expires_at <= moment,
                )
            )
        )
    if database in {"db1", "db2"}:
        statement = statement.where(_database_expression() == database)
    if needs_decision:
        statement = statement.where(
            or_(
                LeadRequest.status.in_(
                    {RequestStatus.pending.value, RequestStatus.failed.value}
                ),
                exists(
                    select(InventoryConflict.id).where(
                        InventoryConflict.status == "pending",
                        or_(
                            InventoryConflict.older_request_id == LeadRequest.id,
                            InventoryConflict.newer_request_id == LeadRequest.id,
                        ),
                    )
                ),
            )
        )
    if q.strip():
        reference = batch_reference_filter(q)
        name = contains(Customer.name, q)
        statement = statement.where(
            or_(reference, name) if reference is not None else name
        )

    total = int(
        db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    )
    pages = max(1, -(-total // page_size))
    page = max(1, min(page, pages))
    is_open = LeadRequest.status.in_(OPEN_STATUSES)
    ordered = (
        statement.order_by(
            case((is_open, 0), else_=1),
            case((is_open, LeadRequest.created_at)).asc(),
            LeadRequest.created_at.desc(),
            LeadRequest.number.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items, databases = batches_with_databases(db, ordered)
    return {
        "items": batch_rows(db, items, databases=databases, now=moment),
        "page": page,
        "pageSize": page_size,
        "total": total,
        "pages": pages,
        "counts": {
            "delivered": int(
                db.scalar(
                    select(func.count(LeadRequest.id)).where(
                        LeadRequest.status == RequestStatus.delivered.value
                    )
                )
                or 0
            ),
            "expired": int(
                db.scalar(
                    select(func.count(LeadRequest.id))
                    .join(BatchArtifact, BatchArtifact.request_id == LeadRequest.id)
                    .where(
                        LeadRequest.status == RequestStatus.delivered.value,
                        BatchArtifact.expires_at <= moment,
                    )
                )
                or 0
            ),
        },
    }


def find_batch(db: Session, reference: str) -> LeadRequest | None:
    """A Batch by number ("1042") or by UUID, the form older links carry."""
    text = reference.strip()
    if text.isdigit():
        if len(text) > 9:
            return None
        return db.scalar(select(LeadRequest).where(LeadRequest.number == int(text)))
    try:
        return db.get(LeadRequest, uuid.UUID(text))
    except ValueError:
        return None


def _older_open(db: Session, item: LeadRequest) -> dict[str, object] | None:
    """Open, unallocated Batches submitted before this one."""
    if item.status not in _UNALLOCATED:
        return None
    statement = (
        select(LeadRequest)
        .options(selectinload(LeadRequest.agent))
        .where(
            LeadRequest.status.in_(
                {
                    RequestStatus.approved.value,
                    RequestStatus.waiting_inventory.value,
                    RequestStatus.processing.value,
                }
            ),
            LeadRequest.created_at < item.created_at,
            LeadRequest.id != item.id,
        )
        .order_by(LeadRequest.created_at.asc())
    )
    count = int(
        db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    )
    first = db.scalar(statement.limit(1))
    return {
        "count": count,
        "first": (
            {
                "number": first.number,
                "customer": first.customer.name,
                "stage": stage(first),
            }
            if first is not None
            else None
        ),
    }


def _cost(db: Session, item: LeadRequest) -> dict[str, object]:
    hold = item.billing_hold
    if not item.is_billed:
        return {"billed": False}
    return {
        "billed": True,
        "rateCentsPerThousand": item.lead_rate_cents_per_thousand,
        "amountCents": item.billing_amount_cents,
        "hold": hold.status if hold is not None else None,
        "availableBalanceCents": available_balance_cents(db, item.agent_id),
    }


def _progress(item: LeadRequest, committed: int) -> list[dict[str, object]]:
    artifact = item.artifact
    steps = [
        ("submitted", "Submitted", True, item.created_at),
        (
            "approved",
            "Approved",
            item.approved_at is not None
            or item.status not in {RequestStatus.pending.value, RequestStatus.rejected.value, RequestStatus.canceled.value},
            item.approved_at,
        ),
        (
            "allocated",
            "Allocated",
            committed > 0 or artifact is not None,
            item.processed_at if committed > 0 or artifact is not None else None,
        ),
        (
            "generated",
            "File generated",
            artifact is not None,
            artifact.created_at if artifact is not None else None,
        ),
        (
            "delivered",
            "Delivered",
            item.delivered_at is not None,
            item.delivered_at,
        ),
    ]
    progress = [
        {"key": key, "label": label, "done": bool(done), "at": at if done else None}
        for key, label, done, at in steps
    ]
    ending = {
        RequestStatus.rejected.value: "Rejected",
        RequestStatus.canceled.value: "Canceled",
        RequestStatus.failed.value: (
            "Delivery failed" if artifact is not None else "Failed"
        ),
    }.get(item.status)
    if ending is not None:
        reached = [step for step in progress if step["done"]]
        progress = reached + [
            {"key": "ended", "label": ending, "done": True, "at": item.closed_at}
        ]
    return progress


def describe_batch(
    db: Session, item: LeadRequest, *, now: datetime | None = None
) -> dict[str, object]:
    """The Batch page: the Batch Request contract plus what surrounds it."""
    moment = now or datetime.now(timezone.utc)
    detail = describe_request(db, item, now=moment)
    customer = item.customer
    committed = int(detail["distribution"]["committed"])
    drawn = db.scalar(
        select(DistributionEvent.inventory_database)
        .where(DistributionEvent.request_id == item.id)
        .limit(1)
    )
    snapshot = (
        db.get(CustomerAvailabilitySnapshot, item.agent_id)
        if item.status in _UNALLOCATED
        else None
    )
    recent = list(
        db.scalars(
            select(LeadRequest)
            .options(selectinload(LeadRequest.artifact))
            .where(LeadRequest.agent_id == item.agent_id, LeadRequest.id != item.id)
            .order_by(LeadRequest.created_at.desc())
            .limit(3)
        )
    )
    open_reports = int(
        db.scalar(
            select(func.count(LeadReport.id)).where(
                LeadReport.customer_id == item.agent_id,
                LeadReport.status == "open",
            )
        )
        or 0
    )
    conflicts = _pending_conflict_ids(db, [item])
    stage_name = stage(item, now=moment)
    detail.update(
        {
            "number": item.number,
            "href": batch_href(item),
            "stage": stage_name,
            "needsDecision": _needs_decision(stage_name, item.id in conflicts),
            "database": drawn or customer.lead_database or "db1",
            "customerRecord": {
                "id": customer.id,
                "name": customer.name,
                "href": f"/app/admin/customers/{customer.id}",
            },
            "agencyRecord": (
                {
                    "id": customer.agency.id,
                    "name": customer.agency.name,
                    "href": f"/app/admin/agencies/{customer.agency.id}",
                }
                if customer.agency is not None
                else None
            ),
            "fill": {
                "needed": item.lead_count,
                **availability(item, committed=committed, snapshot=snapshot),
                "olderOpen": _older_open(db, item),
            },
            "cost": _cost(db, item),
            "progress": _progress(item, committed),
            "recentBatches": [
                {
                    "id": str(other.id),
                    "number": other.number,
                    "href": batch_href(other),
                    "leadCount": other.lead_count,
                    "stage": stage(other, now=moment),
                    "createdAt": other.created_at,
                    "deliveredAt": other.delivered_at,
                }
                for other in recent
            ],
            "openLeadReports": open_reports,
        }
    )
    return detail
