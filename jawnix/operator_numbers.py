"""The operator's headline numbers: inventory, batches, customers, money, work.

Read by the Today page's numbers row (and the Operations overview). Every
figure is a small aggregate over operational tables or a stored snapshot; the
usable DB1 and DB2 totals come from ``inventory_snapshots`` because counting
nine million inventory rows never happens in a request.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.orm import Session

from .customer_directory import active_customer_readiness
from .fulfillment import OPEN_STATUSES
from .models import (
    BatchHold,
    CreditLedgerEntry,
    Customer,
    CustomerRegistration,
    ExclusionList,
    InventoryConflict,
    InventorySnapshot,
    Job,
    JobStatus,
    LeadReport,
    LeadRequest,
    RequestStatus,
    ScrapeAnomaly,
    SourceNicheMapping,
)


WINDOW_DAYS = 30


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class UsableInventory(ContractModel):
    usable: int
    as_of: datetime = Field(alias="asOf")


class InventoryNumbers(ContractModel):
    db1: UsableInventory | None
    db2: UsableInventory | None


class OpenBatchNumbers(ContractModel):
    total: int
    awaiting_approval: int = Field(alias="awaitingApproval")
    short_of_inventory: int = Field(alias="shortOfInventory")


class DeliveredNumbers(ContractModel):
    leads: int
    batches: int
    customers: int


class CustomerNumbers(ContractModel):
    active: int
    with_setup_problems: int = Field(alias="withSetupProblems")


class CreditBalanceNumbers(ContractModel):
    available_cents: int = Field(alias="availableCents")
    billed_customers: int = Field(alias="billedCustomers")


class CreditsUsedNumbers(ContractModel):
    cents: int
    prior_cents: int = Field(alias="priorCents")


class DecisionKind(ContractModel):
    kind: str
    label: str
    count: int
    oldest_at: datetime | None = Field(alias="oldestAt")


class DecisionNumbers(ContractModel):
    waiting: int
    oldest_at: datetime | None = Field(alias="oldestAt")
    kinds: list[DecisionKind]


class OperatorNumbers(ContractModel):
    generated_at: datetime = Field(alias="generatedAt")
    window_days: int = Field(alias="windowDays")
    inventory: InventoryNumbers
    open_batches: OpenBatchNumbers = Field(alias="openBatches")
    delivered: DeliveredNumbers
    customers: CustomerNumbers
    credit_balance: CreditBalanceNumbers = Field(alias="creditBalance")
    credits_used: CreditsUsedNumbers = Field(alias="creditsUsed")
    decisions: DecisionNumbers


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _usable(db: Session, database: str) -> UsableInventory | None:
    row = db.get(InventorySnapshot, database)
    if row is None:
        return None
    return UsableInventory(usable=int(row.usable), as_of=_utc(row.computed_at))


def _open_batches(db: Session) -> OpenBatchNumbers:
    counts = dict(
        db.execute(
            select(LeadRequest.status, func.count(LeadRequest.id))
            .where(LeadRequest.status.in_(OPEN_STATUSES))
            .group_by(LeadRequest.status)
        ).all()
    )
    return OpenBatchNumbers(
        total=sum(int(value) for value in counts.values()),
        awaiting_approval=int(counts.get(RequestStatus.pending.value, 0)),
        short_of_inventory=int(
            counts.get(RequestStatus.waiting_inventory.value, 0)
        ),
    )


def _delivered(db: Session, since: datetime) -> DeliveredNumbers:
    leads, batches, customers = db.execute(
        select(
            func.coalesce(func.sum(LeadRequest.lead_count), 0),
            func.count(LeadRequest.id),
            func.count(distinct(LeadRequest.agent_id)),
        ).where(
            LeadRequest.status == RequestStatus.delivered.value,
            LeadRequest.delivered_at >= since,
        )
    ).one()
    return DeliveredNumbers(
        leads=int(leads or 0),
        batches=int(batches or 0),
        customers=int(customers or 0),
    )


def _credit_balance(db: Session) -> CreditBalanceNumbers:
    """Available balance (ledger minus active Batch Holds) of Billed Customers.

    Billed matches ``billing.stripe_billing_enabled``: billing switched on and
    drawing from DB1. A switched-off wallet keeps its balance invisibly.
    """
    billed = select(Customer.id).where(
        Customer.deleted_at.is_(None),
        Customer.billing_enabled.is_(True),
        Customer.lead_database == "db1",
    )
    ledger = db.scalar(
        select(func.coalesce(func.sum(CreditLedgerEntry.amount_cents), 0))
        .where(CreditLedgerEntry.customer_id.in_(billed))
    )
    holds = db.scalar(
        select(func.coalesce(func.sum(BatchHold.amount_cents), 0)).where(
            BatchHold.customer_id.in_(billed),
            BatchHold.status == "active",
        )
    )
    count = db.scalar(select(func.count()).select_from(billed.subquery()))
    return CreditBalanceNumbers(
        available_cents=int(ledger or 0) - int(holds or 0),
        billed_customers=int(count or 0),
    )


def _credits_used(db: Session, *, since: datetime, prior_since: datetime) -> CreditsUsedNumbers:
    def charged(start: datetime, end: datetime | None = None) -> int:
        statement = select(
            func.coalesce(func.sum(CreditLedgerEntry.amount_cents), 0)
        ).where(
            CreditLedgerEntry.kind == "batch_charge",
            CreditLedgerEntry.created_at >= start,
        )
        if end is not None:
            statement = statement.where(CreditLedgerEntry.created_at < end)
        # Batch Charges are negative ledger entries.
        return -int(db.scalar(statement) or 0)

    return CreditsUsedNumbers(
        cents=charged(since),
        prior_cents=charged(prior_since, since),
    )


def waiting_decisions(db: Session) -> list[DecisionKind]:
    """Every kind of item that waits on an operator decision, oldest first.

    A failed Batch Request is one decision (retry or cancel), so the failed
    jobs behind it are not counted again. Eligibility Holds resolve with
    their Lead Report and are not separate decisions.
    """
    failed_request = select(LeadRequest.id).where(
        LeadRequest.status == RequestStatus.failed.value
    )
    sources = (
        (
            "approval",
            "Approvals",
            select(func.count(LeadRequest.id), func.min(LeadRequest.created_at))
            .where(LeadRequest.status == RequestStatus.pending.value),
        ),
        (
            "failedBatch",
            "Failed batches",
            select(
                func.count(LeadRequest.id),
                func.min(
                    func.coalesce(LeadRequest.closed_at, LeadRequest.created_at)
                ),
            ).where(LeadRequest.status == RequestStatus.failed.value),
        ),
        (
            "inventoryConflict",
            "Inventory conflicts",
            select(
                func.count(InventoryConflict.id),
                func.min(InventoryConflict.created_at),
            ).where(InventoryConflict.status == "pending"),
        ),
        (
            "leadReport",
            "Lead reports",
            select(func.count(LeadReport.id), func.min(LeadReport.created_at))
            .where(LeadReport.status == "open"),
        ),
        (
            "failedJob",
            "Failed jobs",
            select(func.count(Job.id), func.min(Job.created_at)).where(
                Job.status == JobStatus.failed.value,
                or_(
                    Job.request_id.is_(None),
                    Job.request_id.not_in(failed_request),
                ),
            ),
        ),
        (
            "scrapeAnomaly",
            "Scrape anomalies",
            select(
                func.count(ScrapeAnomaly.id),
                func.min(ScrapeAnomaly.created_at),
            ).where(ScrapeAnomaly.status == "pending"),
        ),
        (
            "nicheMapping",
            "Niche mappings",
            select(
                func.count(SourceNicheMapping.id),
                func.min(SourceNicheMapping.updated_at),
            ).where(
                and_(
                    SourceNicheMapping.confirmed.is_(False),
                    SourceNicheMapping.denied_at.is_(None),
                )
            ),
        ),
        (
            "exclusionList",
            "Exclusion lists",
            select(
                func.count(ExclusionList.id),
                func.min(ExclusionList.created_at),
            ).where(ExclusionList.status == "pending_confirmation"),
        ),
        (
            "registration",
            "Registrations",
            select(
                func.count(CustomerRegistration.auth_user_id),
                func.min(CustomerRegistration.created_at),
            ).where(CustomerRegistration.status == "awaiting_review"),
        ),
    )
    kinds = []
    for kind, label, statement in sources:
        count, oldest = db.execute(statement).one()
        kinds.append(
            DecisionKind(
                kind=kind,
                label=label,
                count=int(count or 0),
                oldest_at=_utc(oldest) if count else None,
            )
        )
    return kinds


def _decisions(db: Session) -> DecisionNumbers:
    kinds = waiting_decisions(db)
    oldest = [kind.oldest_at for kind in kinds if kind.oldest_at is not None]
    return DecisionNumbers(
        waiting=sum(kind.count for kind in kinds),
        oldest_at=min(oldest) if oldest else None,
        kinds=kinds,
    )


def operator_numbers(
    db: Session,
    *,
    now: datetime | None = None,
) -> OperatorNumbers:
    """The eight headline figures, each with its supporting counts."""
    moment = _utc(now) or datetime.now(timezone.utc)
    since = moment - timedelta(days=WINDOW_DAYS)
    prior_since = since - timedelta(days=WINDOW_DAYS)
    active, with_problems = active_customer_readiness(db)
    return OperatorNumbers(
        generated_at=moment,
        window_days=WINDOW_DAYS,
        inventory=InventoryNumbers(
            db1=_usable(db, "db1"),
            db2=_usable(db, "db2"),
        ),
        open_batches=_open_batches(db),
        delivered=_delivered(db, since),
        customers=CustomerNumbers(
            active=active,
            with_setup_problems=with_problems,
        ),
        credit_balance=_credit_balance(db),
        credits_used=_credits_used(
            db,
            since=since,
            prior_since=prior_since,
        ),
        decisions=_decisions(db),
    )
