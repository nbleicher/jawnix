"""Insights: the operator's weekly review (P6).

Money, deliveries, and reports over the last 30 days against the 30 before;
revenue by month; how each Customer's deliveries are trending; which Source
Segments produce good leads; and the Nightly Review history.

Every figure is a small aggregate over Batch Requests, the Credit Ledger,
Lead Reports, or stored nightly snapshots. Nothing here counts Distribution
Events or inventory rows: a month is about two million delivered leads, which
the Batch Requests that delivered them already sum.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, union
from sqlalchemy.orm import Session

from .acquisition import review_dict
from .batches import batch_rows
from .models import (
    CreditLedgerEntry,
    Customer,
    DailySourcePerformance,
    LeadDispositionTransition,
    LeadOutcome,
    LeadReport,
    LeadRequest,
    NightlyReview,
    RequestStatus,
)


WINDOW_DAYS = 30

#: Revenue by month covers this many calendar months, ending with this one.
REVENUE_MONTHS = 6

#: The busiest Customers shown under Customer trends.
TREND_LIMIT = 10

#: Source Segments shown on Insights; the full table is one link away.
SOURCE_LIMIT = 10

#: Enough Nightly Reviews to see two weeks at a glance.
NIGHTLY_REVIEW_LIMIT = 14


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ledger_sum(
    db: Session, kind: str, start: datetime, end: datetime | None = None
) -> tuple[int, int]:
    """The sum and count of one kind of ledger entry in ``[start, end)``."""
    statement = select(
        func.coalesce(func.sum(CreditLedgerEntry.amount_cents), 0),
        func.count(CreditLedgerEntry.id),
    ).where(
        CreditLedgerEntry.kind == kind,
        CreditLedgerEntry.created_at >= start,
    )
    if end is not None:
        statement = statement.where(CreditLedgerEntry.created_at < end)
    total, count = db.execute(statement).one()
    return int(total or 0), int(count or 0)


def _delivered(
    db: Session, start: datetime, end: datetime | None = None
) -> tuple[int, int]:
    """Leads and Batches delivered in ``[start, end)``."""
    statement = select(
        func.coalesce(func.sum(LeadRequest.lead_count), 0),
        func.count(LeadRequest.id),
    ).where(
        LeadRequest.status == RequestStatus.delivered.value,
        LeadRequest.delivered_at >= start,
    )
    if end is not None:
        statement = statement.where(LeadRequest.delivered_at < end)
    leads, batches = db.execute(statement).one()
    return int(leads or 0), int(batches or 0)


def _reports(db: Session, start: datetime, end: datetime | None = None) -> int:
    statement = select(func.count(LeadReport.id)).where(
        LeadReport.created_at >= start
    )
    if end is not None:
        statement = statement.where(LeadReport.created_at < end)
    return int(db.scalar(statement) or 0)


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _previous_month(moment: datetime) -> datetime:
    return _month_start(_month_start(moment) - timedelta(days=1))


def _revenue_by_month(db: Session, now: datetime) -> list[dict[str, object]]:
    """Credits used and leads delivered per calendar month (UTC), oldest
    first. Months before the first one with any activity are left out."""
    starts = [_month_start(now)]
    while len(starts) < REVENUE_MONTHS:
        starts.append(_previous_month(starts[-1]))
    starts.reverse()
    first = starts[0]

    def bucket(moment: datetime | None) -> int | None:
        value = _utc(moment)
        if value is None or value < first:
            return None
        index = (value.year - first.year) * 12 + value.month - first.month
        return index if 0 <= index < len(starts) else None

    used = [0] * len(starts)
    leads = [0] * len(starts)
    batches = [0] * len(starts)
    for amount, created_at in db.execute(
        select(CreditLedgerEntry.amount_cents, CreditLedgerEntry.created_at).where(
            CreditLedgerEntry.kind == "batch_charge",
            CreditLedgerEntry.created_at >= first,
        )
    ):
        if (index := bucket(created_at)) is not None:
            # Batch Charges are negative ledger entries.
            used[index] -= int(amount)
    for count, delivered_at in db.execute(
        select(LeadRequest.lead_count, LeadRequest.delivered_at).where(
            LeadRequest.status == RequestStatus.delivered.value,
            LeadRequest.delivered_at >= first,
        )
    ):
        if (index := bucket(delivered_at)) is not None:
            leads[index] += int(count)
            batches[index] += 1

    months = []
    for index, start in enumerate(starts):
        current = index == len(starts) - 1
        months.append(
            {
                "month": f"{start.year:04d}-{start.month:02d}",
                "creditsUsedCents": used[index],
                "leadsDelivered": leads[index],
                "batches": batches[index],
                # The current month so far, so a short month is not read as
                # a bad one.
                "daysSoFar": now.day if current else None,
            }
        )
    while months and not (
        months[0]["creditsUsedCents"] or months[0]["leadsDelivered"]
    ):
        months.pop(0)
    return months


def _customer_trends(
    db: Session, since: datetime, prior_since: datetime
) -> list[dict[str, object]]:
    """Each Customer's leads delivered against the prior 30 days, their Lead
    Reports, and any open Batch short of inventory, busiest first."""
    recent = LeadRequest.delivered_at >= since
    delivered: dict[int, list[int]] = {
        customer_id: [int(current or 0), int(prior or 0)]
        for customer_id, current, prior in db.execute(
            select(
                LeadRequest.agent_id,
                func.sum(case((recent, LeadRequest.lead_count), else_=0)),
                func.sum(case((recent, 0), else_=LeadRequest.lead_count)),
            )
            .where(
                LeadRequest.status == RequestStatus.delivered.value,
                LeadRequest.delivered_at >= prior_since,
            )
            .group_by(LeadRequest.agent_id)
        )
    }
    reports = dict(
        db.execute(
            select(LeadReport.customer_id, func.count(LeadReport.id))
            .where(LeadReport.created_at >= since)
            .group_by(LeadReport.customer_id)
        ).all()
    )
    waiting = list(
        db.scalars(
            select(LeadRequest).where(
                LeadRequest.status == RequestStatus.waiting_inventory.value
            )
        )
    )
    short: dict[int, int] = {}
    for row in batch_rows(db, waiting):
        fill = row["availability"]
        if fill.get("kind") == "short":
            customer_id = row["customer"]["id"]
            short[customer_id] = short.get(customer_id, 0) + int(fill["short"])

    ids = set(delivered) | set(short)
    names = {
        customer.id: customer
        for customer in db.scalars(select(Customer).where(Customer.id.in_(ids)))
    }
    rows = [
        {
            "customer": {
                "id": customer_id,
                "name": names[customer_id].name,
                "href": f"/app/admin/customers/{customer_id}",
            },
            "delivered": delivered.get(customer_id, [0, 0])[0],
            "priorDelivered": delivered.get(customer_id, [0, 0])[1],
            "reports": int(reports.get(customer_id, 0)),
            "shortBy": short.get(customer_id),
        }
        for customer_id in ids
        if customer_id in names
    ]
    rows.sort(
        key=lambda row: (
            -int(row["delivered"]),
            -int(row["priorDelivered"]),
            str(row["customer"]["name"]).casefold(),
        )
    )
    return rows[:TREND_LIMIT]


def _rate(numerator: int, denominator: int) -> float | None:
    """A rate only when there is something to divide by: no outcomes is not
    a 0% outcome rate."""
    return numerator / denominator if denominator else None


def _outcomes_reported(db: Session) -> int:
    """Distribution Events a Customer has reported any outcome for."""
    events = union(
        select(LeadDispositionTransition.distribution_event_id),
        select(LeadOutcome.distribution_event_id),
    ).subquery()
    return int(db.scalar(select(func.count()).select_from(events)) or 0)


def _source_performance(db: Session) -> dict[str, object]:
    """The latest nightly Source Performance snapshot, busiest segments first.

    The snapshot covers the trailing 90-day Source Cohort; rates stay empty
    until a segment has the Worked Leads or Quality Ratings they divide by.
    """
    latest = db.scalar(select(func.max(DailySourcePerformance.snapshot_date)))
    if latest is None:
        return {
            "snapshotDate": None,
            "windowDays": None,
            "segments": 0,
            "outcomesReported": _outcomes_reported(db),
            "rows": [],
        }
    rows = list(
        db.scalars(
            select(DailySourcePerformance).where(
                DailySourcePerformance.snapshot_date == latest
            )
        )
    )
    rows.sort(
        key=lambda item: (
            -int((item.counts or {}).get("delivered", 0)),
            item.segment_key,
        )
    )
    window = None
    if rows:
        started = _utc(rows[0].window_started_at)
        ended = _utc(rows[0].window_ended_at)
        if started is not None and ended is not None:
            window = round((ended - started).total_seconds() / 86_400)

    def row(item: DailySourcePerformance) -> dict[str, object]:
        counts = {key: int(value or 0) for key, value in (item.counts or {}).items()}
        worked = counts.get("worked", 0)
        rated = counts.get("rated", 0)
        return {
            "segment": item.segment_key,
            "keyword": item.keyword,
            "state": item.state,
            "niche": item.niche,
            "nicheConfirmed": item.niche_confirmed,
            "delivered": counts.get("delivered", 0),
            "worked": worked,
            "rated": rated,
            "positive": counts.get("positive", 0),
            "booked": counts.get("booked", 0),
            "poor": counts.get("poor", 0),
            "positiveRate": _rate(counts.get("positive", 0), worked),
            "bookedRate": _rate(counts.get("booked", 0), worked),
            "poorRate": _rate(counts.get("poor", 0), rated),
        }

    return {
        "snapshotDate": latest.isoformat(),
        "windowDays": window,
        "segments": len(rows),
        "outcomesReported": _outcomes_reported(db),
        "rows": [row(item) for item in rows[:SOURCE_LIMIT]],
    }


def _nightly_reviews(db: Session) -> list[dict[str, object]]:
    reviews = db.scalars(
        select(NightlyReview)
        .order_by(NightlyReview.created_at.desc(), NightlyReview.id)
        .limit(NIGHTLY_REVIEW_LIMIT)
    )
    return [review_dict(review) for review in reviews]


def operator_insights(
    db: Session,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Everything the Insights page shows, in one read."""
    moment = _utc(now) or datetime.now(timezone.utc)
    since = moment - timedelta(days=WINDOW_DAYS)
    prior_since = since - timedelta(days=WINDOW_DAYS)

    used, _ = _ledger_sum(db, "batch_charge", since)
    prior_used, _ = _ledger_sum(db, "batch_charge", prior_since, since)
    bought, purchases = _ledger_sum(db, "purchase", since)
    prior_bought, prior_purchases = _ledger_sum(db, "purchase", prior_since, since)
    leads, batches = _delivered(db, since)
    prior_leads, prior_batches = _delivered(db, prior_since, since)

    return {
        "generatedAt": moment,
        "windowDays": WINDOW_DAYS,
        # Batch Charges are negative ledger entries.
        "creditsUsed": {"cents": -used, "priorCents": -prior_used},
        "creditsBought": {
            "cents": bought,
            "priorCents": prior_bought,
            "purchases": purchases,
            "priorPurchases": prior_purchases,
        },
        "delivered": {
            "leads": leads,
            "priorLeads": prior_leads,
            "batches": batches,
            "priorBatches": prior_batches,
        },
        "leadReports": {
            "count": _reports(db, since),
            "priorCount": _reports(db, prior_since, since),
        },
        "revenueByMonth": _revenue_by_month(db, moment),
        "customerTrends": _customer_trends(db, since, prior_since),
        "sourcePerformance": _source_performance(db),
        "nightlyReviews": _nightly_reviews(db),
    }
