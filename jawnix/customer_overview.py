"""The Customer's Home, in one read.

Home answers what a Customer opens the portal for: what they can spend, where
they are appointed, what arrived this month, the batch in progress, and the
files waiting to be downloaded, with alerts only for what needs them now (a
file about to expire, a balance too low for their usual batch, setup that is
not finished). The same read is the Customer shell's access check.

Supply stays hidden: nothing here counts inventory or says what is available.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .billing import _wallet_totals, batch_cost_cents, stripe_billing_enabled
from .customer_history import request_history_scope
from .fulfillment import artifact_available
from .models import (
    AuditEntry,
    BatchArtifact,
    BatchHold,
    CreditLedgerEntry,
    CustomerProfile,
    LeadRequest,
    RequestStatus,
)
from .schemas import CustomerOverviewStatus
from .states import normalize_states


_REQUEST_STATUS: dict[str, CustomerOverviewStatus] = {
    RequestStatus.pending.value: CustomerOverviewStatus(
        label="Submitted",
        description="Your request is ready for review.",
        tone="info",
    ),
    RequestStatus.approved.value: CustomerOverviewStatus(
        label="Approved",
        description="Your request was approved and is being prepared.",
        tone="info",
    ),
    RequestStatus.processing.value: CustomerOverviewStatus(
        label="Preparing Batch",
        description="We are preparing your leads for delivery.",
        tone="warning",
    ),
    RequestStatus.waiting_inventory.value: CustomerOverviewStatus(
        label="Preparing Batch",
        description=(
            "We are waiting for enough matching leads. "
            "There is nothing you need to do."
        ),
        tone="warning",
    ),
    RequestStatus.generated.value: CustomerOverviewStatus(
        label="Preparing Batch",
        description="Your Batch is prepared and being delivered.",
        tone="warning",
    ),
    RequestStatus.delivered.value: CustomerOverviewStatus(
        label="Delivered",
        description="Your Batch has been delivered.",
        tone="success",
    ),
    RequestStatus.rejected.value: CustomerOverviewStatus(
        label="Not Approved",
        description="This request was not approved. You can submit a new request.",
        tone="danger",
    ),
    RequestStatus.canceled.value: CustomerOverviewStatus(
        label="Canceled",
        description="This request was canceled. You can submit a new request.",
        tone="neutral",
    ),
    RequestStatus.failed.value: CustomerOverviewStatus(
        label="Needs Attention",
        description=(
            "We could not complete this request. "
            "Email noah@jawnix.com before trying again."
        ),
        tone="danger",
    ),
}


def customer_request_status(status: str) -> CustomerOverviewStatus:
    """Translate fulfillment state into the public Customer vocabulary."""

    return _REQUEST_STATUS.get(
        status,
        CustomerOverviewStatus(
            label="Submitted",
            description="We are reviewing your request. There is nothing you need to do.",
            tone="info",
        ),
    )


ARTIFACT_EXPIRING_SOON = timedelta(days=7)

#: Home lists this many of the most recently delivered batches.
RECENT_BATCH_LIMIT = 5

#: With no batch to compare against, a balance is low below this many leads.
LOW_BALANCE_FALLBACK_LEADS = 1_000

#: Submitted and not yet delivered or closed: what the in-progress panel shows.
IN_PROGRESS_STATUSES = frozenset(
    {
        RequestStatus.pending.value,
        RequestStatus.approved.value,
        RequestStatus.processing.value,
        RequestStatus.waiting_inventory.value,
        RequestStatus.generated.value,
    }
)


# --- The Home contract -------------------------------------------------------


class CustomerHomeBatch(BaseModel):
    """One batch as Home shows it.

    ``number`` names it "Batch #N" once batch numbers exist; until then the
    client falls back to its short reference.
    """

    id: uuid.UUID
    number: int | None = None
    lead_count: int
    states: list[str]
    submitted_at: datetime
    delivered_at: datetime | None
    # The frozen charge, or None when the batch was not billed.
    cost_cents: int | None
    href: str
    file_available: bool
    file_expires_at: datetime | None
    download_href: str | None


class CustomerHomeStep(BaseModel):
    key: Literal["submitted", "approval", "preparing", "ready"]
    label: str
    state: Literal["complete", "current", "upcoming"]
    occurred_at: datetime | None


class CustomerHomeInProgress(BaseModel):
    batch: CustomerHomeBatch
    steps: list[CustomerHomeStep]
    note: str
    # What this batch holds from the balance until it is delivered.
    held_cents: int | None
    can_cancel: bool


class CustomerHomeAlert(BaseModel):
    """Something on Home that needs the Customer now.

    ``file_expiring`` carries the batch, ``setup_incomplete`` the problem, and
    ``low_balance`` the batch size the balance falls short of (None when the
    Customer has not requested one yet).
    """

    kind: Literal["file_expiring", "low_balance", "setup_incomplete"]
    batch: CustomerHomeBatch | None = None
    problem: (
        Literal["no_appointed_states", "account_pending", "account_inactive"]
        | None
    ) = None
    reference_lead_count: int | None = None


class CustomerHomeBatchRef(BaseModel):
    id: uuid.UUID
    number: int | None = None


class CustomerHomeBilling(BaseModel):
    """The Credit Wallet in Customer words. Absent for unbilled Customers."""

    available_balance_cents: int
    held_cents: int
    held_for: list[CustomerHomeBatchRef]
    lead_rate_cents_per_thousand: int | None
    # The most leads one batch could buy from the available balance.
    covers_leads: int | None
    spent_this_month_cents: int


class CustomerHomeMonth(BaseModel):
    starts_at: datetime
    delivered_leads: int
    delivered_batches: int


class CustomerHome(BaseModel):
    customer_name: str
    appointed_states: list[str]
    can_request: bool
    billing: CustomerHomeBilling | None
    month: CustomerHomeMonth
    alerts: list[CustomerHomeAlert]
    in_progress: list[CustomerHomeInProgress]
    recent_batches: list[CustomerHomeBatch]
    batch_count: int


# --- Building it ---------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def parse_month_start(value: str | None) -> datetime | None:
    """The browser's local month start, or None when it sent nothing usable."""

    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def month_start_for(requested: datetime | None, now: datetime) -> datetime:
    """Where "this month" starts for the Customer.

    The browser sends the start of its local month so the figures follow the
    Customer's own calendar. A value in the future or more than a month back
    is not a month start, and falls back to the UTC month.
    """

    utc_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if requested is None:
        return utc_month
    value = _as_utc(requested)
    if value > now or now - value > timedelta(days=32):
        return utc_month
    return value


def covers_leads(available_cents: int, rate_cents_per_thousand: int) -> int:
    """The most leads one batch could buy, inverting ``batch_cost_cents``.

    A batch of ``n`` leads costs ``(n * rate + 500) // 1000`` cents with a 1¢
    floor, so the largest affordable ``n`` is ``(1000 * available + 499) //
    rate`` once there is at least a cent to spend.
    """

    if available_cents <= 0 or rate_cents_per_thousand <= 0:
        return 0
    return (available_cents * 1_000 + 499) // rate_cents_per_thousand


def _batch_number(item: LeadRequest) -> int | None:
    # Batch numbers arrive with the operator's batch work; read them when the
    # column exists so this contract needs no change when they do.
    return getattr(item, "number", None)


def home_batch(item: LeadRequest, *, now: datetime) -> CustomerHomeBatch:
    delivered = item.status == RequestStatus.delivered.value
    artifact = item.artifact if delivered else None
    available = artifact_available(artifact, now=now)
    return CustomerHomeBatch(
        id=item.id,
        number=_batch_number(item),
        lead_count=item.lead_count,
        states=normalize_states(item.states_snapshot),
        submitted_at=item.created_at,
        delivered_at=item.delivered_at,
        cost_cents=item.billing_amount_cents if item.is_billed else None,
        href=f"/app/requests?request={item.id}",
        file_available=available,
        file_expires_at=artifact.expires_at if artifact is not None else None,
        download_href=(
            f"/api/me/batch-requests/{item.id}/artifact" if available else None
        ),
    )


_IN_PROGRESS_NOTE = {
    RequestStatus.pending.value: (
        "Noah reviews each request, then we deliver the full batch at once "
        "and email you."
    ),
    RequestStatus.waiting_inventory.value: (
        "Approved. We are waiting for enough matching leads to deliver the "
        "full batch at once. There is nothing you need to do; we email you "
        "when it is ready."
    ),
}
_PREPARING_NOTE = (
    "Approved. We are preparing your leads and will email you when the full "
    "batch is ready to download."
)


def _steps(item: LeadRequest) -> list[CustomerHomeStep]:
    """The four steps a Customer was promised, with where this batch is."""

    approved = item.status != RequestStatus.pending.value
    return [
        CustomerHomeStep(
            key="submitted",
            label="Submitted",
            state="complete",
            occurred_at=item.created_at,
        ),
        CustomerHomeStep(
            key="approval",
            label="Approved" if approved else "Waiting for approval",
            state="complete" if approved else "current",
            occurred_at=item.approved_at if approved else None,
        ),
        CustomerHomeStep(
            key="preparing",
            label="Preparing your leads",
            state="current" if approved else "upcoming",
            occurred_at=None,
        ),
        CustomerHomeStep(
            key="ready",
            label="Ready to download",
            state="upcoming",
            occurred_at=None,
        ),
    ]


def _in_progress(item: LeadRequest, *, now: datetime) -> CustomerHomeInProgress:
    # Imported here: customer_requests reads this module's status vocabulary.
    from .customer_requests import CANCELABLE_STATUSES

    hold = item.billing_hold
    return CustomerHomeInProgress(
        batch=home_batch(item, now=now),
        steps=_steps(item),
        note=_IN_PROGRESS_NOTE.get(item.status, _PREPARING_NOTE),
        held_cents=(
            hold.amount_cents
            if hold is not None and hold.status == "active"
            else None
        ),
        can_cancel=item.status in CANCELABLE_STATUSES,
    )


def _setup_problem(
    profile: CustomerProfile,
) -> Literal["no_appointed_states", "account_pending", "account_inactive"] | None:
    """Why this Customer cannot request a batch yet, if anything."""

    customer = profile.customer
    if customer is None or profile.mapping_confirmed_at is None:
        return "account_pending"
    agency = customer.agency
    if (
        not customer.active
        or customer.deleted_at is not None
        or (
            agency is not None
            and (not agency.active or agency.deleted_at is not None)
        )
    ):
        return "account_inactive"
    if not normalize_states(profile.licensed_states):
        return "no_appointed_states"
    return None


def _customer_name(profile: CustomerProfile) -> str:
    if profile.customer is not None and profile.customer.name.strip():
        return profile.customer.name.strip()
    name = f"{profile.first_name.strip()} {profile.last_name.strip()}".strip()
    return name or profile.email


def _expiring_files(
    db: Session,
    scope,
    *,
    now: datetime,
) -> list[LeadRequest]:
    """Live files that expire within a week and the Customer never downloaded."""

    candidates = list(
        db.scalars(
            select(LeadRequest)
            .join(BatchArtifact, BatchArtifact.request_id == LeadRequest.id)
            .where(
                scope,
                LeadRequest.status == RequestStatus.delivered.value,
                BatchArtifact.expires_at > now,
                BatchArtifact.expires_at <= now + ARTIFACT_EXPIRING_SOON,
            )
            .options(selectinload(LeadRequest.artifact))
            .order_by(BatchArtifact.expires_at, LeadRequest.id)
        )
    )
    if not candidates:
        return []
    # Downloads belong to the Customer, not the User Account that made them:
    # only the Customer's own logins can download its batches, so any download
    # recorded against one of these requests counts, including downloads by a
    # User Account that has since been replaced.
    downloaded = set(
        db.scalars(
            select(AuditEntry.target_id).where(
                AuditEntry.action == "batch_artifact_downloaded",
                AuditEntry.target_type == "batch_request",
                AuditEntry.target_id.in_([str(item.id) for item in candidates]),
            )
        )
    )
    return [
        item
        for item in candidates
        if str(item.id) not in downloaded
        and artifact_available(item.artifact, now=now)
    ]


def _billing(
    db: Session,
    profile: CustomerProfile,
    *,
    month_start: datetime,
) -> CustomerHomeBilling | None:
    customer = profile.customer
    if customer is None or not stripe_billing_enabled(customer):
        return None
    _, held, available = _wallet_totals(db, customer.id)
    held_for = [
        CustomerHomeBatchRef(id=item.id, number=_batch_number(item))
        for item in db.scalars(
            select(LeadRequest)
            .join(BatchHold, BatchHold.request_id == LeadRequest.id)
            .where(
                BatchHold.customer_id == customer.id,
                BatchHold.status == "active",
            )
            .order_by(LeadRequest.created_at, LeadRequest.id)
        )
    ]
    spent = int(
        db.scalar(
            select(func.coalesce(func.sum(CreditLedgerEntry.amount_cents), 0))
            .where(
                CreditLedgerEntry.customer_id == customer.id,
                CreditLedgerEntry.kind == "batch_charge",
                CreditLedgerEntry.created_at >= month_start,
            )
        )
        or 0
    )
    rate = customer.lead_rate_cents_per_thousand
    return CustomerHomeBilling(
        available_balance_cents=available,
        held_cents=held,
        held_for=held_for,
        lead_rate_cents_per_thousand=rate,
        covers_leads=covers_leads(available, rate) if rate else None,
        # Charges are negative ledger entries; spending reads as positive.
        spent_this_month_cents=-spent,
    )


def build_customer_home(
    db: Session,
    *,
    user_id: uuid.UUID,
    profile: CustomerProfile,
    month_start: datetime | None = None,
    now: datetime | None = None,
) -> CustomerHome:
    """Everything the Customer's Home renders, in one read."""

    moment = now or datetime.now(timezone.utc)
    starts_at = month_start_for(month_start, moment)
    scope = request_history_scope(profile, user_id)

    batch_count = int(
        db.scalar(select(func.count()).select_from(LeadRequest).where(scope))
        or 0
    )
    in_progress = list(
        db.scalars(
            select(LeadRequest)
            .where(scope, LeadRequest.status.in_(IN_PROGRESS_STATUSES))
            .options(selectinload(LeadRequest.billing_hold))
            .order_by(LeadRequest.created_at.desc(), LeadRequest.id.desc())
        )
    )
    recent = list(
        db.scalars(
            select(LeadRequest)
            .where(scope, LeadRequest.status == RequestStatus.delivered.value)
            .options(selectinload(LeadRequest.artifact))
            .order_by(
                LeadRequest.delivered_at.desc(),
                LeadRequest.created_at.desc(),
                LeadRequest.id.desc(),
            )
            .limit(RECENT_BATCH_LIMIT)
        )
    )
    delivered_leads, delivered_batches = db.execute(
        select(
            func.coalesce(func.sum(LeadRequest.lead_count), 0),
            func.count(),
        ).where(
            scope,
            LeadRequest.status == RequestStatus.delivered.value,
            LeadRequest.delivered_at >= starts_at,
        )
    ).one()
    billing = _billing(db, profile, month_start=starts_at)

    alerts = [
        CustomerHomeAlert(kind="file_expiring", batch=home_batch(item, now=moment))
        for item in _expiring_files(db, scope, now=moment)
    ]
    problem = _setup_problem(profile)
    if problem is not None:
        alerts.append(CustomerHomeAlert(kind="setup_incomplete", problem=problem))
    if billing is not None and billing.lead_rate_cents_per_thousand:
        latest = db.scalar(
            select(LeadRequest.lead_count)
            .where(scope)
            .order_by(LeadRequest.created_at.desc(), LeadRequest.id.desc())
            .limit(1)
        )
        needed = batch_cost_cents(
            latest or LOW_BALANCE_FALLBACK_LEADS,
            billing.lead_rate_cents_per_thousand,
        )
        if billing.available_balance_cents < needed:
            alerts.append(
                CustomerHomeAlert(kind="low_balance", reference_lead_count=latest)
            )

    return CustomerHome(
        customer_name=_customer_name(profile),
        appointed_states=normalize_states(profile.licensed_states),
        can_request=problem is None,
        billing=billing,
        month=CustomerHomeMonth(
            starts_at=starts_at,
            delivered_leads=int(delivered_leads or 0),
            delivered_batches=int(delivered_batches or 0),
        ),
        alerts=alerts,
        in_progress=[_in_progress(item, now=moment) for item in in_progress],
        recent_batches=[home_batch(item, now=moment) for item in recent],
        batch_count=batch_count,
    )
