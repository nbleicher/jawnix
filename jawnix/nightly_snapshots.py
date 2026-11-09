"""Shared Nightly Review building blocks.

Both the scrape-run-scoped writer (``jawnix_data.scraper.create_nightly_review``)
and the calendar-day-scoped writer (``jawnix.nightly.run_scheduled_nightly_review``)
need the same operational inventory snapshot and the same full-count segment
summaries. This module holds that shared logic so neither writer has to
depend on the other's package: ``jawnix_data`` already imports from ``jawnix``
at module scope (see ``jawnix_data/scheduler.py``), so a reverse, module-level
``jawnix -> jawnix_data`` import would risk a circular import. Living here,
under ``jawnix``, keeps the dependency direction one-way.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    DatasetPublication,
    InventoryConflict,
    InventorySnapshot,
    Lead,
    LeadRequest,
    NightlyReview,
    RequestStatus,
    ScrapeSegmentResult,
    SourceRecommendation,
)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _usable_db1(snapshot: InventorySnapshot | None) -> dict:
    """The stored usable DB1 count, never a fresh count of inventory.

    The background refresh owns that count. Omitting the key when it has never
    run lets the Nightly Review say so instead of showing a false zero.
    """
    if snapshot is None:
        return {}
    return {
        "eligible": int(snapshot.usable),
        "eligibleAsOf": _iso(snapshot.computed_at),
    }


def nightly_operational_snapshot(session: Session) -> dict:
    """Inventory totals plus the requests/conflicts/recommendations queues."""
    inventory_by_state = {
        state: int(count)
        for state, count in session.execute(
            select(Lead.state, func.count(Lead.id)).where(Lead.in_db1.is_(True))
            .group_by(Lead.state)
            .order_by(Lead.state)
        )
    }
    return {
        "inventory": {
            "total": int(
                session.scalar(select(func.count(Lead.id)).where(Lead.in_db1.is_(True))) or 0
            ),
            "byState": inventory_by_state,
            **_usable_db1(session.get(InventorySnapshot, "db1")),
        },
        "waitingRequests": [
            {
                "id": str(item.id),
                "customerId": item.customer_id,
                "requested": item.lead_count,
                "available": item.available_count,
                "states": item.states_snapshot,
            }
            for item in session.scalars(
                select(LeadRequest)
                .where(
                    LeadRequest.status
                    == RequestStatus.waiting_inventory.value
                )
                .order_by(LeadRequest.created_at, LeadRequest.id)
            )
        ],
        "inventoryConflicts": [
            {
                "id": str(item.id),
                "status": item.status,
                "olderRequestId": str(item.older_request_id),
                "newerRequestId": str(item.newer_request_id),
            }
            for item in session.scalars(
                select(InventoryConflict)
                .where(
                    InventoryConflict.status.in_(
                        {"pending", "confirmed", "denied"}
                    )
                )
                .order_by(InventoryConflict.created_at)
            )
        ],
        "recommendations": [
            {
                "id": str(item.id),
                "niche": item.niche,
                "segment": item.segment_key,
                "action": item.action,
                "status": item.status,
            }
            for item in session.scalars(
                select(SourceRecommendation)
                .where(SourceRecommendation.status == "pending")
                .order_by(SourceRecommendation.created_at)
            )
        ],
    }


def scrape_run_segment_summaries(
    session: Session,
    scraper_run_id: int,
) -> list[dict]:
    """Full observed/valid/new/duplicate/quarantined/anomalous counts.

    One entry per ``ScrapeSegmentResult`` row recorded for the run, ordered
    by segment key.
    """
    return [
        {
            "key": item.segment_key,
            "niche": item.niche,
            "geography": item.geography,
            "observed": item.observed_count,
            "valid": item.valid_count,
            "new": item.new_count,
            "duplicate": item.duplicate_count,
            "quarantined": item.quarantined_count,
            "anomalous": item.anomalous,
            "anomalyReasons": item.anomaly_reasons,
        }
        for item in session.scalars(
            select(ScrapeSegmentResult)
            .where(ScrapeSegmentResult.scraper_run_id == scraper_run_id)
            .order_by(ScrapeSegmentResult.segment_key)
        )
    ]


def settle_nightly_review_inventory(
    session: Session,
    snapshot: InventorySnapshot,
) -> NightlyReview | None:
    """Give the latest synchronized run's review the post-sync usable count.

    An Inventory Sync writes its run's Nightly Review in the same transaction
    that queues the usable-inventory refresh, so that review can only quote the
    count from before the sync. Once a refresh computed after the sync lands,
    copy it in. Reviews whose count already postdates their sync are left as
    the durable record they are.
    """
    publication = session.scalar(
        select(DatasetPublication)
        .where(
            DatasetPublication.sync_status == "complete",
            DatasetPublication.synchronized_at.is_not(None),
        )
        .order_by(DatasetPublication.synchronized_at.desc())
        .limit(1)
    )
    if publication is None:
        return None
    synchronized_at = publication.synchronized_at
    if synchronized_at.tzinfo is None:
        synchronized_at = synchronized_at.replace(tzinfo=timezone.utc)
    computed_at = snapshot.computed_at
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)
    if computed_at < synchronized_at:
        return None
    review = session.scalar(
        select(NightlyReview).where(
            NightlyReview.scraper_run_id == publication.scraper_run_id
        )
    )
    if review is None:
        return None
    inventory = (review.summary or {}).get("inventory")
    if not isinstance(inventory, dict):
        return None
    recorded = _parse_iso(inventory.get("eligibleAsOf"))
    if recorded is not None and recorded >= synchronized_at:
        return None
    review.summary = {
        **review.summary,
        "inventory": {**inventory, **_usable_db1(snapshot)},
    }
    session.flush()
    return review
