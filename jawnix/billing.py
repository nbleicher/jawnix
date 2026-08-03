from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .activity import record_activity
from .models import (
    BatchHold,
    CreditLedgerEntry,
    Customer,
    LeadRequest,
    utcnow,
)


MIN_LEAD_RATE_CENTS_PER_THOUSAND = 100
MAX_LEAD_RATE_CENTS_PER_THOUSAND = 2_000


class BillingError(Exception):
    def __init__(self, detail: str, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class SubmissionBilling:
    is_billed: bool
    lead_rate_cents_per_thousand: int | None = None
    amount_cents: int | None = None


def batch_cost_cents(
    lead_count: int,
    lead_rate_cents_per_thousand: int,
) -> int:
    """Round a quantity at the Lead Rate to the nearest whole cent.

    Adding half the divisor gives deterministic half-up rounding without
    introducing floating point money.
    """

    return (
        lead_count * lead_rate_cents_per_thousand + 500
    ) // 1_000


def _wallet_totals(db: Session, customer_id: int) -> tuple[int, int, int]:
    balance = int(
        db.scalar(
            select(func.coalesce(func.sum(CreditLedgerEntry.amount_cents), 0))
            .where(CreditLedgerEntry.customer_id == customer_id)
        )
        or 0
    )
    active_holds = int(
        db.scalar(
            select(func.coalesce(func.sum(BatchHold.amount_cents), 0)).where(
                BatchHold.customer_id == customer_id,
                BatchHold.status == "active",
            )
        )
        or 0
    )
    return balance, active_holds, balance - active_holds


def wallet_view(db: Session, customer: Customer) -> dict[str, object]:
    balance, active_holds, available = _wallet_totals(db, customer.id)
    entries = db.scalars(
        select(CreditLedgerEntry)
        .where(CreditLedgerEntry.customer_id == customer.id)
        .order_by(
            CreditLedgerEntry.created_at.desc(),
            CreditLedgerEntry.id.desc(),
        )
    )
    return {
        "customerId": customer.id,
        "billingEnabled": customer.billing_enabled,
        "leadRateCentsPerThousand": (
            customer.lead_rate_cents_per_thousand
        ),
        "balanceCents": balance,
        "activeHoldsCents": active_holds,
        "availableBalanceCents": available,
        "ledger": [
            {
                "id": str(entry.id),
                "kind": entry.kind,
                "amountCents": entry.amount_cents,
                "reason": entry.reason,
                "actor": entry.actor_user_id,
                "batchRequestId": (
                    str(entry.batch_request_id)
                    if entry.batch_request_id is not None
                    else None
                ),
                "createdAt": entry.created_at,
            }
            for entry in entries
        ],
    }


def prepare_submission(
    db: Session,
    *,
    customer_id: int,
    lead_count: int,
) -> SubmissionBilling:
    """Freeze billing terms and verify that a new hold can be covered.

    Locking the Customer serializes hold placement and wallet writes. The
    available-balance check therefore cannot race another billed submission
    on PostgreSQL, even when no prior hold row exists to lock.
    """

    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None:
        raise BillingError("Customer was not found.", 404)
    if not customer.billing_enabled:
        return SubmissionBilling(is_billed=False)
    rate = customer.lead_rate_cents_per_thousand
    if rate is None:
        raise BillingError(
            "Billing is enabled, but this Customer has no Lead Rate."
        )
    amount = batch_cost_cents(lead_count, rate)
    _, _, available = _wallet_totals(db, customer.id)
    if available < amount:
        raise BillingError(
            "The Credit Wallet does not have enough available balance for "
            f"this Batch Hold. Required {amount} cents; available "
            f"{available} cents."
        )
    return SubmissionBilling(
        is_billed=True,
        lead_rate_cents_per_thousand=rate,
        amount_cents=amount,
    )


def place_batch_hold(
    db: Session,
    *,
    request: LeadRequest,
    billing: SubmissionBilling,
) -> BatchHold | None:
    request.is_billed = billing.is_billed
    request.lead_rate_cents_per_thousand = (
        billing.lead_rate_cents_per_thousand
    )
    request.billing_amount_cents = billing.amount_cents
    if not billing.is_billed:
        return None
    if billing.amount_cents is None:
        raise RuntimeError("A billed request must freeze its Batch cost.")
    hold = BatchHold(
        request=request,
        customer_id=request.customer_id,
        amount_cents=billing.amount_cents,
        status="active",
    )
    db.add(hold)
    return hold


def capture_batch_hold(db: Session, request: LeadRequest) -> None:
    """Capture a request's active hold in the distribution transaction."""

    if not request.is_billed:
        return
    hold = db.scalar(
        select(BatchHold)
        .where(BatchHold.request_id == request.id)
        .with_for_update()
    )
    if hold is None:
        raise RuntimeError("Billed Batch Request has no Batch Hold.")
    if hold.status == "captured":
        return
    if hold.status != "active":
        raise RuntimeError("Released Batch Hold cannot be captured.")
    existing_charge = db.scalar(
        select(CreditLedgerEntry).where(
            CreditLedgerEntry.batch_request_id == request.id
        )
    )
    if existing_charge is None:
        db.add(
            CreditLedgerEntry(
                customer_id=hold.customer_id,
                kind="batch_charge",
                amount_cents=-hold.amount_cents,
                reason=f"Batch Hold captured for Batch Request {request.id}.",
                actor_user_id="system:allocation",
                batch_request_id=request.id,
            )
        )
    hold.status = "captured"
    hold.captured_at = utcnow()


def release_batch_hold(db: Session, request: LeadRequest) -> None:
    """Release an uncommitted request without changing its Credit Ledger."""

    if not request.is_billed:
        return
    hold = db.scalar(
        select(BatchHold)
        .where(BatchHold.request_id == request.id)
        .with_for_update()
    )
    if hold is None or hold.status != "active":
        return
    hold.status = "released"
    hold.released_at = utcnow()


def configure_customer_billing(
    db: Session,
    *,
    customer_id: int,
    billing_enabled: bool,
    lead_rate_cents_per_thousand: int | None,
    actor_id: uuid.UUID,
    reason: str,
) -> Customer:
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None or customer.deleted_at is not None:
        raise BillingError("Customer was not found.", 404)
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise BillingError("A reason is required for a billing change.", 422)
    if billing_enabled and lead_rate_cents_per_thousand is None:
        raise BillingError(
            "A Lead Rate is required when billing is enabled.",
            422,
        )
    if lead_rate_cents_per_thousand is not None and not (
        MIN_LEAD_RATE_CENTS_PER_THOUSAND
        <= lead_rate_cents_per_thousand
        <= MAX_LEAD_RATE_CENTS_PER_THOUSAND
    ):
        raise BillingError(
            "Lead Rate must be between 100 and 2000 cents per 1,000 leads.",
            422,
        )
    before = {
        "billingEnabled": customer.billing_enabled,
        "leadRateCentsPerThousand": (
            customer.lead_rate_cents_per_thousand
        ),
    }
    customer.billing_enabled = billing_enabled
    customer.lead_rate_cents_per_thousand = (
        lead_rate_cents_per_thousand
    )
    after = {
        "billingEnabled": customer.billing_enabled,
        "leadRateCentsPerThousand": (
            customer.lead_rate_cents_per_thousand
        ),
    }
    record_activity(
        db,
        action="customer_billing_updated",
        target_type="customer",
        target_id=customer.id,
        actor_id=actor_id,
        reason=normalized_reason,
        details={"before": before, "after": after},
    )
    return customer


def post_admin_adjustment(
    db: Session,
    *,
    customer_id: int,
    amount_cents: int,
    actor_id: uuid.UUID,
    reason: str,
) -> CreditLedgerEntry:
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
    )
    if customer is None or customer.deleted_at is not None:
        raise BillingError("Customer was not found.", 404)
    if amount_cents == 0:
        raise BillingError("An adjustment must change the wallet balance.", 422)
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise BillingError("A reason is required for an adjustment.", 422)
    before, _, _ = _wallet_totals(db, customer.id)
    entry = CreditLedgerEntry(
        customer_id=customer.id,
        kind="admin_adjustment",
        amount_cents=amount_cents,
        reason=normalized_reason,
        actor_user_id=str(actor_id),
    )
    db.add(entry)
    db.flush()
    record_activity(
        db,
        action="credit_wallet_adjusted",
        target_type="customer",
        target_id=customer.id,
        actor_id=actor_id,
        reason=normalized_reason,
        details={
            "ledgerEntryId": str(entry.id),
            "amountCents": amount_cents,
            "before": {"balanceCents": before},
            "after": {"balanceCents": before + amount_cents},
        },
    )
    return entry
