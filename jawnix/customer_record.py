"""The operator's Customer figures: what each Customer needs, and its numbers.

The Customers table and the Customer page read the same figures, so the Needs
column and the Needs tile always agree. Every figure is a small indexed read
over Batch Requests, the Credit Ledger, Batch Holds, Lead Reports, Inventory
Conflicts, and the background availability snapshot. Nothing here counts
inventory: "available now" is the Customer's last availability snapshot, with
its as-of time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session, selectinload

from .activity import record_activity
from .batches import availability, batch_href, stage
from .billing import stripe_billing_enabled
from .eligibility import _REPORT_REASONS
from .fulfillment import OPEN_STATUSES
from .jobs import enqueue_job
from .models import (
    BatchHold,
    CreditLedgerEntry,
    Customer,
    CustomerAvailabilitySnapshot,
    DistributionEvent,
    EligibilityHold,
    InventoryConflict,
    LeadReport,
    LeadRequest,
    RequestStatus,
    UserAccount,
    UserAccountInvitation,
)

#: Delivered leads and batches are counted over this many days.
WINDOW_DAYS = 30

#: Needs in the order they are worked: a decision only the operator can make
#: first, then what is stuck, then what is only incomplete.
NEED_ORDER = (
    "approval",
    "deliveryFailed",
    "failedBatch",
    "short",
    "conflict",
    "report",
    "setup",
)
_NEED_RANK = {kind: index for index, kind in enumerate(NEED_ORDER)}

#: Setup problems as one short operator line each.
_SETUP_LABELS = {
    "no_account": "Setup · no user account",
    "invitation": "Setup · invitation not accepted",
    "no_states": "Setup · no appointed states",
    "agency": "Setup · agency deactivated",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


@dataclass
class CustomerNeed:
    kind: str
    label: str
    href: str
    since: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "label": self.label, "href": self.href}


@dataclass
class CustomerFigures:
    """One Customer's numbers, as both the table and the record page show them."""

    needs: list[CustomerNeed] = field(default_factory=list)
    billed: bool = False
    #: Available balance: the Credit Ledger less active Batch Holds, the
    #: figure the Customer sees as their balance.
    balance_cents: int = 0
    held_cents: int = 0
    #: Batch numbers whose holds make up ``held_cents``.
    held_for: list[int] = field(default_factory=list)
    rate_cents_per_thousand: int | None = None
    open_batches: int = 0
    open_batch: dict[str, object] | None = None
    delivered_leads: int = 0
    delivered_batches: int = 0
    last_delivery_at: datetime | None = None
    available: int | None = None
    available_as_of: datetime | None = None

    def sort_key(self) -> tuple:
        """Customers that need something first, worst need first."""
        if not self.needs:
            return (1, 0, datetime.min.replace(tzinfo=timezone.utc))
        first = self.needs[0]
        return (
            0,
            _NEED_RANK.get(first.kind, len(NEED_ORDER)),
            first.since or datetime.min.replace(tzinfo=timezone.utc),
        )


def setup_needs(
    customer: Customer,
    account: UserAccount | None,
    invitation: UserAccountInvitation | None,
) -> list[CustomerNeed]:
    """What stops an active Customer from working, in short operator lines.

    A deactivated Customer is not expected to work, so it has no setup needs.
    """
    if not customer.active:
        return []
    href = f"/app/admin/customers/{customer.id}"
    keys: list[tuple[str, str]] = []
    if account is None and invitation is None:
        keys.append(("no_account", f"{href}/access"))
    elif account is None:
        keys.append(("invitation", f"{href}/access"))
    if not customer.licensed_states:
        keys.append(("no_states", f"{href}/rules"))
    if customer.agency is not None and not customer.agency.active:
        keys.append(("agency", href))
    return [
        CustomerNeed(kind="setup", label=_SETUP_LABELS[key], href=target)
        for key, target in keys
    ]


def _batch_needs(
    item: LeadRequest,
    snapshot: CustomerAvailabilitySnapshot | None,
) -> CustomerNeed | None:
    href = batch_href(item)
    if item.status == RequestStatus.pending.value:
        return CustomerNeed("approval", f"Approve #{item.number}", href, _utc(item.created_at))
    if item.status == RequestStatus.failed.value:
        since = _utc(item.closed_at or item.created_at)
        if item.artifact is not None:
            return CustomerNeed("deliveryFailed", f"Re-notify #{item.number}", href, since)
        return CustomerNeed("failedBatch", f"#{item.number} failed", href, since)
    if item.status == RequestStatus.waiting_inventory.value:
        fill = availability(item, committed=None, snapshot=snapshot)
        label = (
            f"#{item.number} short {int(fill['short']):,}"
            if fill["kind"] == "short"
            else f"#{item.number} waiting for inventory"
        )
        return CustomerNeed("short", label, href, _utc(item.created_at))
    return None


def _report_label(reasons: list[str]) -> str:
    if len(reasons) > 1:
        return f"{len(reasons):,} open reports"
    label = _REPORT_REASONS.get(reasons[0], reasons[0].replace("_", " "))
    return f"Report · {label[:1].lower()}{label[1:]}"


def customer_figures(
    db: Session,
    customers: list[Customer],
    *,
    accounts: dict[int, UserAccount] | None = None,
    invitations: dict[int, UserAccountInvitation] | None = None,
    now: datetime | None = None,
) -> dict[int, CustomerFigures]:
    """Figures for many Customers in a fixed number of bulk reads."""
    moment = now or datetime.now(timezone.utc)
    ids = [customer.id for customer in customers]
    figures = {customer.id: CustomerFigures() for customer in customers}
    if not ids:
        return figures

    snapshots = {
        row.customer_id: row
        for row in db.scalars(
            select(CustomerAvailabilitySnapshot).where(
                CustomerAvailabilitySnapshot.customer_id.in_(ids)
            )
        )
    }

    # Open batches, oldest first: the order they wait in.
    open_items = list(
        db.scalars(
            select(LeadRequest)
            .options(selectinload(LeadRequest.artifact))
            .where(
                LeadRequest.agent_id.in_(ids),
                LeadRequest.status.in_(OPEN_STATUSES),
            )
            .order_by(LeadRequest.created_at.asc(), LeadRequest.number.asc())
        )
    )
    for item in open_items:
        figure = figures[item.agent_id]
        figure.open_batches += 1
        if figure.open_batch is None:
            figure.open_batch = {
                "number": item.number,
                "leadCount": item.lead_count,
                "stage": stage(item, now=moment),
                "href": batch_href(item),
            }
        need = _batch_needs(item, snapshots.get(item.agent_id))
        if need is not None:
            figure.needs.append(need)

    # Pending conflicts name the other Customer, the one this Customer's
    # batch is competing with.
    conflicts = list(
        db.execute(
            select(
                InventoryConflict.id,
                InventoryConflict.created_at,
                InventoryConflict.older_request_id,
                InventoryConflict.newer_request_id,
            ).where(InventoryConflict.status == "pending")
        )
    )
    if conflicts:
        request_ids = {row.older_request_id for row in conflicts} | {
            row.newer_request_id for row in conflicts
        }
        requests = {
            row.id: row
            for row in db.execute(
                select(LeadRequest.id, LeadRequest.agent_id, LeadRequest.number)
                .where(LeadRequest.id.in_(request_ids))
            )
        }
        names = dict(
            db.execute(
                select(Customer.id, Customer.name).where(
                    Customer.id.in_({row.agent_id for row in requests.values()})
                )
            ).all()
        )
        for conflict in conflicts:
            older = requests.get(conflict.older_request_id)
            newer = requests.get(conflict.newer_request_id)
            if older is None or newer is None:
                continue
            href = f"/app/admin/fulfillment/conflicts/{conflict.id}"
            for mine, theirs in ((older, newer), (newer, older)):
                if mine.agent_id not in figures:
                    continue
                label = (
                    f"Conflict between #{older.number} and #{newer.number}"
                    if mine.agent_id == theirs.agent_id
                    else f"Conflict with {names.get(theirs.agent_id, 'another customer')}"
                )
                if any(need.href == href for need in figures[mine.agent_id].needs):
                    continue
                figures[mine.agent_id].needs.append(
                    CustomerNeed("conflict", label, href, _utc(conflict.created_at))
                )

    reports: dict[int, list[LeadReport]] = {}
    for report in db.scalars(
        select(LeadReport)
        .where(LeadReport.customer_id.in_(ids), LeadReport.status == "open")
        .order_by(LeadReport.created_at.asc())
    ):
        reports.setdefault(report.customer_id, []).append(report)
    for customer_id, items in reports.items():
        first = items[0]
        figures[customer_id].needs.append(
            CustomerNeed(
                "report",
                _report_label([item.reason for item in items]),
                (
                    f"/app/admin/fulfillment/reports/{first.id}"
                    if len(items) == 1
                    else f"/app/admin/customers/{customer_id}/reports"
                ),
                _utc(first.created_at),
            )
        )

    accounts = accounts if accounts is not None else _active_accounts(db, ids)
    invitations = (
        invitations if invitations is not None else _pending_invitations(db, ids)
    )
    for customer in customers:
        figures[customer.id].needs.extend(
            setup_needs(
                customer,
                accounts.get(customer.id),
                invitations.get(customer.id),
            )
        )

    # Money: the Credit Ledger less active holds, as the Customer sees it.
    ledger = dict(
        db.execute(
            select(
                CreditLedgerEntry.customer_id,
                func.coalesce(func.sum(CreditLedgerEntry.amount_cents), 0),
            )
            .where(CreditLedgerEntry.customer_id.in_(ids))
            .group_by(CreditLedgerEntry.customer_id)
        ).all()
    )
    holds: dict[int, list[tuple[int, int]]] = {}
    for customer_id, amount, number in db.execute(
        select(BatchHold.customer_id, BatchHold.amount_cents, LeadRequest.number)
        .join(LeadRequest, LeadRequest.id == BatchHold.request_id)
        .where(BatchHold.customer_id.in_(ids), BatchHold.status == "active")
        .order_by(LeadRequest.number.asc())
    ):
        holds.setdefault(customer_id, []).append((int(amount), int(number)))

    since = moment - timedelta(days=WINDOW_DAYS)
    delivered = {
        row.agent_id: row
        for row in db.execute(
            select(
                LeadRequest.agent_id,
                func.coalesce(func.sum(LeadRequest.lead_count), 0).label("leads"),
                func.count(LeadRequest.id).label("batches"),
            )
            .where(
                LeadRequest.agent_id.in_(ids),
                LeadRequest.status == RequestStatus.delivered.value,
                LeadRequest.delivered_at >= since,
            )
            .group_by(LeadRequest.agent_id)
        )
    }
    last_delivery = dict(
        db.execute(
            select(LeadRequest.agent_id, func.max(LeadRequest.delivered_at))
            .where(
                LeadRequest.agent_id.in_(ids),
                LeadRequest.delivered_at.is_not(None),
            )
            .group_by(LeadRequest.agent_id)
        ).all()
    )

    for customer in customers:
        figure = figures[customer.id]
        figure.needs.sort(
            key=lambda need: (
                _NEED_RANK.get(need.kind, len(NEED_ORDER)),
                need.since or datetime.min.replace(tzinfo=timezone.utc),
            )
        )
        held = holds.get(customer.id, [])
        figure.billed = stripe_billing_enabled(customer)
        figure.held_cents = sum(amount for amount, _ in held)
        figure.held_for = [number for _, number in held]
        figure.balance_cents = int(ledger.get(customer.id, 0) or 0) - figure.held_cents
        figure.rate_cents_per_thousand = customer.lead_rate_cents_per_thousand
        row = delivered.get(customer.id)
        if row is not None:
            figure.delivered_leads = int(row.leads or 0)
            figure.delivered_batches = int(row.batches or 0)
        figure.last_delivery_at = _utc(last_delivery.get(customer.id))
        snapshot = snapshots.get(customer.id)
        if snapshot is not None:
            figure.available = int(snapshot.available)
            figure.available_as_of = _utc(snapshot.computed_at)
    return figures


def _active_accounts(db: Session, ids: list[int]) -> dict[int, UserAccount]:
    return {
        account.customer_id: account
        for account in db.scalars(
            select(UserAccount).where(
                UserAccount.active.is_(True),
                UserAccount.customer_id.in_(ids),
            )
        )
        if account.customer_id is not None
    }


def _pending_invitations(
    db: Session, ids: list[int]
) -> dict[int, UserAccountInvitation]:
    return {
        invitation.customer_id: invitation
        for invitation in db.scalars(
            select(UserAccountInvitation).where(
                UserAccountInvitation.status == "pending",
                UserAccountInvitation.customer_id.in_(ids),
            )
        )
    }


def _customer_since(db: Session, customer_id: int) -> datetime | None:
    """The earliest trace of the Customer: its first access or first batch.

    Customers carry no creation time of their own.
    """
    candidates = [
        db.scalar(
            select(func.min(UserAccount.created_at)).where(
                UserAccount.customer_id == customer_id
            )
        ),
        db.scalar(
            select(func.min(LeadRequest.created_at)).where(
                LeadRequest.agent_id == customer_id
            )
        ),
    ]
    known = [_utc(value) for value in candidates if value is not None]
    return min(known) if known else None


def customer_summary(
    db: Session, customer: Customer, *, now: datetime | None = None
) -> dict[str, object]:
    """The Customer page's numbers row and Needs list in one read."""
    figure = customer_figures(db, [customer], now=now)[customer.id]
    open_reports, total_reports = db.execute(
        select(
            func.coalesce(
                func.sum(case((LeadReport.status == "open", 1), else_=0)), 0
            ),
            func.count(LeadReport.id),
        ).where(LeadReport.customer_id == customer.id)
    ).one()
    return {
        "customerId": customer.id,
        "database": customer.lead_database or "db1",
        "states": list(customer.licensed_states or []),
        "customerSince": _customer_since(db, customer.id),
        "needs": [need.as_dict() for need in figure.needs],
        "balance": {
            "billed": figure.billed,
            "availableCents": figure.balance_cents,
            "heldCents": figure.held_cents,
            "heldFor": figure.held_for,
            "rateCentsPerThousand": figure.rate_cents_per_thousand,
        },
        "available": (
            {"count": figure.available, "asOf": figure.available_as_of}
            if figure.available is not None
            else None
        ),
        "openBatches": {"count": figure.open_batches, "first": figure.open_batch},
        "delivered": {
            "windowDays": WINDOW_DAYS,
            "leads": figure.delivered_leads,
            "batches": figure.delivered_batches,
        },
        "lastDeliveryAt": figure.last_delivery_at,
        "leadReports": {
            "open": int(open_reports or 0),
            "total": int(total_reports or 0),
        },
    }


_REPORT_STATUS_LABELS = {
    "open": "Open",
    "dismiss": "Dismissed",
    "dismissed": "Dismissed",
    "correct": "Corrected",
    "corrected": "Corrected",
    "suppress": "Suppressed",
    "suppressed": "Suppressed",
}


def customer_lead_reports(
    db: Session, customer: Customer, *, limit: int = 200
) -> list[dict[str, object]]:
    """Every Lead Report this Customer filed, newest first, with its batch."""
    reports = list(
        db.scalars(
            select(LeadReport)
            .where(LeadReport.customer_id == customer.id)
            .order_by(LeadReport.created_at.desc(), LeadReport.id)
            .limit(limit)
        )
    )
    if not reports:
        return []
    events = {
        row.id: row
        for row in db.execute(
            select(
                DistributionEvent.id,
                DistributionEvent.phone,
                DistributionEvent.title,
                DistributionEvent.state,
                DistributionEvent.request_id,
            ).where(
                DistributionEvent.id.in_(
                    {item.distribution_event_id for item in reports}
                )
            )
        )
    }
    request_ids = {
        row.request_id for row in events.values() if row.request_id is not None
    }
    numbers = (
        dict(
            db.execute(
                select(LeadRequest.id, LeadRequest.number).where(
                    LeadRequest.id.in_(request_ids)
                )
            ).all()
        )
        if request_ids
        else {}
    )
    held = set(
        db.scalars(
            select(EligibilityHold.report_id).where(
                EligibilityHold.report_id.in_({item.id for item in reports}),
                EligibilityHold.active.is_(True),
            )
        )
    )
    rows = []
    for item in reports:
        event = events.get(item.distribution_event_id)
        number = numbers.get(event.request_id) if event is not None else None
        rows.append(
            {
                "id": str(item.id),
                "href": f"/app/admin/fulfillment/reports/{item.id}",
                "reason": item.reason,
                "reasonLabel": _REPORT_REASONS.get(item.reason, item.reason),
                "details": item.details,
                "status": item.status,
                "statusLabel": _REPORT_STATUS_LABELS.get(
                    item.status, item.status.replace("_", " ").capitalize()
                ),
                "createdAt": _utc(item.created_at),
                "lead": (
                    {"phone": event.phone, "title": event.title, "state": event.state}
                    if event is not None
                    else None
                ),
                "batch": (
                    {"number": number, "href": f"/app/admin/batches/{number}"}
                    if number is not None
                    else None
                ),
                "eligibilityHeld": item.id in held,
            }
        )
    return rows


def _actor_label(actor: str | None) -> str:
    """Who posted a ledger entry, in words. The ledger stores identities."""
    value = (actor or "").strip()
    if value == "system:stripe":
        return "Stripe"
    if value.startswith("system"):
        return "System"
    if "@" in value:
        return value
    return "Operator" if value else "—"


def operator_wallet(db: Session, wallet: dict[str, object]) -> dict[str, object]:
    """The Credit Wallet with each ledger entry's actor and batch in words.

    Added only on the operator's read: the Customer's own wallet keeps the
    shared shape.
    """
    ledger = list(wallet.get("ledger") or [])
    request_ids = {
        uuid.UUID(str(entry["batchRequestId"]))
        for entry in ledger
        if entry.get("batchRequestId")
    }
    numbers = (
        {
            str(request_id): number
            for request_id, number in db.execute(
                select(LeadRequest.id, LeadRequest.number).where(
                    LeadRequest.id.in_(request_ids)
                )
            )
        }
        if request_ids
        else {}
    )
    return {
        **wallet,
        "ledger": [
            {
                **entry,
                "actorLabel": _actor_label(entry.get("actor")),
                "batchNumber": numbers.get(str(entry.get("batchRequestId") or "")),
            }
            for entry in ledger
        ],
    }


class LeadDatabaseChangeError(ValueError):
    pass


def change_lead_database(
    db: Session,
    *,
    customer: Customer,
    database: str,
    actor_id: object,
    reason: str,
) -> bool:
    """Point a Customer's future allocations at DB1 or DB2.

    Returns whether anything changed. The Customer's availability snapshot
    counted the old database, so it is dropped, and rotation is woken so a
    waiting batch can try the new one.
    """
    if database not in {"db1", "db2"}:
        raise LeadDatabaseChangeError("Choose DB1 or DB2.")
    reason = reason.strip()
    if not reason:
        raise LeadDatabaseChangeError("Reason is required.")
    before = customer.lead_database or "db1"
    if before == database:
        return False
    customer.lead_database = database
    record_activity(
        db,
        action="customer_lead_database_changed",
        target_type="customer",
        target_id=customer.id,
        actor_id=actor_id,
        reason=reason,
        details={"before": {"lead_database": before}, "after": {"lead_database": database}},
    )
    db.execute(
        delete(CustomerAvailabilitySnapshot).where(
            CustomerAvailabilitySnapshot.customer_id == customer.id
        )
    )
    enqueue_job(db, "fulfill_round_robin")
    return True


__all__ = [
    "NEED_ORDER",
    "WINDOW_DAYS",
    "CustomerFigures",
    "CustomerNeed",
    "LeadDatabaseChangeError",
    "change_lead_database",
    "customer_figures",
    "customer_lead_reports",
    "customer_summary",
    "operator_wallet",
    "setup_needs",
]
