from __future__ import annotations

from datetime import date, datetime
from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ..activity import ACTIVITY_PAGE_SIZE, activity_filters, query_activity
from ..auth import Principal, require_admin
from ..config import Settings, get_settings
from ..database import get_db
from ..batches import describe_batch, find_batch, list_batches
from ..operations_overview import OperationsOverview, operations_overview
from ..search import search as operator_search
from ..today import today as operator_today
from ..insights import operator_insights
from ..operator_setup import operator_setup
from fastapi import APIRouter


router = APIRouter()


@router.get(
    "/api/admin/operations-overview",
    response_model=OperationsOverview,
)
def admin_operations_overview(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One resilient aggregate for the administrator Operations overview."""
    return operations_overview(db)


@router.get("/api/admin/today")
def admin_today(
    delivered_since: datetime | None = Query(default=None, alias="deliveredSince"),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The Today page in one read: numbers, last night, decisions, batches.

    ``deliveredSince`` is the operator's local midnight, so "delivered today"
    follows their day rather than the server's.
    """
    return operator_today(db, delivered_since=delivered_since)


@router.get("/api/admin/insights")
def admin_insights(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The Insights page in one read: money, deliveries, reports, trends,
    Source Performance, and the Nightly Review history."""
    return operator_insights(db)


@router.get("/api/admin/setup")
def admin_setup(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """The Setup index: each occasional tool's state and last change."""
    return operator_setup(db, settings)


@router.get("/api/admin/search")
def admin_search(
    q: str = Query(default="", max_length=200),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Grouped matches for the header search box."""
    return operator_search(db, q)


@router.get("/api/admin/batches")
def admin_batches(
    q: str = Query(default="", max_length=200),
    status: str = "open",
    database: str = "",
    needs_decision: bool = Query(default=False, alias="needsDecision"),
    customer: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """All batches, filtered and paged, oldest open batch first.

    ``customer`` narrows to one Customer's batches, for the Customer page.
    """
    return list_batches(
        db,
        q=q,
        status=status,
        database=database,
        needs_decision=needs_decision,
        customer_id=customer,
        page=page,
        page_size=page_size,
    )


@router.get("/api/admin/batches/{reference}")
def admin_batch_detail(
    reference: str,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """One Batch by number, or by the UUID older links carry."""
    item = find_batch(db, reference)
    if item is None:
        raise HTTPException(status_code=404, detail="Batch was not found.")
    return describe_batch(db, item)


@router.get("/api/admin/activity")
def admin_activity(
    q: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    entity_type: str | None = Query(default=None, alias="entityType"),
    entity_id: str | None = Query(default=None, alias="entityId"),
    date_from: date | None = Query(default=None, alias="dateFrom"),
    date_to: date | None = Query(default=None, alias="dateTo"),
    who: str | None = Query(default=None, max_length=160),
    refused: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=ACTIVITY_PAGE_SIZE,
        alias="pageSize",
        ge=1,
        le=100,
    ),
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Global administrator Activity, filtered and paginated on the server.

    ``filters`` carries the choices the page offers: every recorded record
    type and everyone who has acted.
    """
    return {
        **query_activity(
            db,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
            query=q,
            who=who,
            refused=refused,
        ),
        "filters": activity_filters(db),
    }
