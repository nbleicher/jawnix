"""The Credit Wallet as the Customer's Account shows it (Billing tab).

``wallet_view`` is the wallet record the header widget and the operator read.
This is the Customer's Billing tab: four numbers (available balance, held,
rate, spent in the last 30 days) and one history in Customer words, where
every batch is named by its number and links to its page.

The history walks the available balance back in time. Active Batch Holds sit
in it beside the ledger, because they are why the available balance is lower
than the ledger total: "Held for batch #1042". A captured hold leaves the
history and its "Charged for batch #1042" ledger entry takes its place; a
released hold never moved the balance and is not shown. Every row's balance
therefore reconciles with the row above it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .billing import _wallet_totals, stripe_billing_enabled
from .models import BatchHold, CreditLedgerEntry, Customer, LeadRequest

#: "Spent · 30 days" on the Billing tab.
SPENT_WINDOW = timedelta(days=30)
#: Newest history rows returned. A Customer buys a few times a month, so this
#: is years of history; older rows are left off rather than paged.
HISTORY_LIMIT = 100

_LEDGER_KIND = {
    "purchase": "purchase",
    "batch_charge": "charge",
    "admin_adjustment": "adjustment",
}


class CustomerBillingBatch(BaseModel):
    """A batch named by its number, with its page in the portal."""

    id: uuid.UUID
    number: int
    href: str


class CustomerBillingEntry(BaseModel):
    id: str
    kind: Literal["purchase", "charge", "hold", "adjustment"]
    occurred_at: datetime
    #: Signed: credits are positive, charges and holds negative.
    amount_cents: int
    #: The available balance once this row had happened.
    balance_after_cents: int
    batch: CustomerBillingBatch | None = None
    #: The reason the operator gave for an adjustment.
    note: str | None = None


class CustomerBilling(BaseModel):
    available_balance_cents: int
    held_cents: int
    held_for: list[CustomerBillingBatch]
    lead_rate_cents_per_thousand: int | None
    spent_since: datetime
    spent_cents: int
    spent_batches: int
    history: list[CustomerBillingEntry]
    #: False when older rows were left off (more than HISTORY_LIMIT).
    history_complete: bool


def batch_href(request_id: uuid.UUID) -> str:
    return f"/app/batches/{request_id}"


def _batch(request_id: uuid.UUID | None, number: int | None) -> CustomerBillingBatch | None:
    if request_id is None or number is None:
        return None
    return CustomerBillingBatch(id=request_id, number=number, href=batch_href(request_id))


def build_customer_billing(
    db: Session,
    customer: Customer,
    *,
    now: datetime | None = None,
) -> CustomerBilling | None:
    """The Billing tab in one read, or None when the Customer is not billed.

    Billing applies exactly when the header shows the Credit Wallet: billing
    is on and the Customer draws from DB1.
    """

    if not stripe_billing_enabled(customer):
        return None
    moment = now or datetime.now(timezone.utc)
    _, held, available = _wallet_totals(db, customer.id)

    holds = db.execute(
        select(BatchHold, LeadRequest.number)
        .join(LeadRequest, LeadRequest.id == BatchHold.request_id)
        .where(
            BatchHold.customer_id == customer.id,
            BatchHold.status == "active",
        )
        .order_by(BatchHold.created_at, BatchHold.id)
    ).all()
    # One more than the limit says whether anything older was left off.
    entries = db.execute(
        select(CreditLedgerEntry, LeadRequest.number)
        .outerjoin(LeadRequest, LeadRequest.id == CreditLedgerEntry.batch_request_id)
        .where(CreditLedgerEntry.customer_id == customer.id)
        .order_by(CreditLedgerEntry.created_at.desc(), CreditLedgerEntry.id.desc())
        .limit(HISTORY_LIMIT + 1)
    ).all()

    rows: list[tuple[datetime, int, str, CustomerBillingEntry]] = []
    for hold, number in holds:
        rows.append((
            _aware(hold.created_at),
            1,
            str(hold.id),
            CustomerBillingEntry(
                id=f"hold:{hold.id}",
                kind="hold",
                occurred_at=hold.created_at,
                amount_cents=-hold.amount_cents,
                balance_after_cents=0,
                batch=_batch(hold.request_id, number),
            ),
        ))
    for entry, number in entries:
        kind = _LEDGER_KIND.get(entry.kind, "adjustment")
        rows.append((
            _aware(entry.created_at),
            0,
            str(entry.id),
            CustomerBillingEntry(
                id=str(entry.id),
                kind=kind,  # type: ignore[arg-type]
                occurred_at=entry.created_at,
                amount_cents=entry.amount_cents,
                balance_after_cents=0,
                batch=_batch(entry.batch_request_id, number),
                note=(entry.reason or None) if kind == "adjustment" else None,
            ),
        ))
    # Newest first. At the same instant a hold reads as after the ledger
    # entry: a purchase lands, then a batch is held against it.
    rows.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    complete = len(rows) <= HISTORY_LIMIT
    history: list[CustomerBillingEntry] = []
    balance = available
    for _, _, _, item in rows[:HISTORY_LIMIT]:
        history.append(item.model_copy(update={"balance_after_cents": balance}))
        balance -= item.amount_cents

    since = moment - SPENT_WINDOW
    spent, spent_batches = db.execute(
        select(
            func.coalesce(func.sum(CreditLedgerEntry.amount_cents), 0),
            func.count(),
        ).where(
            CreditLedgerEntry.customer_id == customer.id,
            CreditLedgerEntry.kind == "batch_charge",
            CreditLedgerEntry.created_at >= since,
        )
    ).one()

    return CustomerBilling(
        available_balance_cents=available,
        held_cents=held,
        held_for=[
            batch
            for hold, number in holds
            if (batch := _batch(hold.request_id, number)) is not None
        ],
        lead_rate_cents_per_thousand=customer.lead_rate_cents_per_thousand,
        spent_since=since,
        # Charges are negative ledger entries; spending reads as positive.
        spent_cents=-int(spent or 0),
        spent_batches=int(spent_batches or 0),
        history=history,
        history_complete=complete,
    )


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; compare everything as UTC."""

    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
