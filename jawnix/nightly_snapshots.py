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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    InventoryConflict,
    Lead,
    LeadRequest,
    RequestStatus,
    ScrapeSegmentResult,
    SourceRecommendation,
)


def nightly_operational_snapshot(session: Session) -> dict:
    """Inventory totals plus the requests/conflicts/recommendations queues."""
    inventory_by_state = {
        state: int(count)
        for state, count in session.execute(
            select(Lead.state, func.count(Lead.id))
            .group_by(Lead.state)
            .order_by(Lead.state)
        )
    }
    return {
        "inventory": {
            "total": int(
                session.scalar(select(func.count(Lead.id))) or 0
            ),
            "byState": inventory_by_state,
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
