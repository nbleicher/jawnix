"""Admin-only pool analytics: breakdown, per-Customer availability, cooldown forecast.

Counts are persisted with an as-of timestamp and served from that snapshot on
GET. Computation runs only on explicit refresh (or draft projection), never as
a side effect of loading an admin page.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from .allocation import _same_recipient_clause, inventory_count
from .config import Settings
from .models import (
    Customer,
    CustomerAvailabilitySnapshot,
    DistributionEvent,
    EligibilityHold,
    ExclusionList,
    ExclusionPhone,
    Lead,
    LeadRequest,
    ListingObservation,
    NicheAssignment,
    PoolBreakdownSnapshot,
    RequestStatus,
    SourceNicheMapping,
    utcnow,
)
from .niche_policy import effective_niche_expression, policy_clause


UNMAPPED_NICHE = "unmapped"
FORECAST_DAYS = 14
_POOL_BREAKDOWN_ID = 1


def _as_of_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _actively_held():
    return exists(
        select(EligibilityHold.id).where(
            EligibilityHold.lead_id == Lead.id,
            EligibilityHold.active.is_(True),
        )
    )


def _globally_excluded():
    return exists(
        select(ExclusionPhone.phone)
        .join(
            ExclusionList,
            ExclusionList.id == ExclusionPhone.exclusion_list_id,
        )
        .where(
            ExclusionPhone.phone == Lead.phone,
            ExclusionList.global_effective.is_(True),
        )
    )


def compute_pool_breakdown(session: Session) -> list[dict]:
    """Inventory composition by state × effective Niche (unmapped as its bucket)."""
    niche = effective_niche_expression()
    niche_label = func.coalesce(niche, UNMAPPED_NICHE)
    rows = session.execute(
        select(Lead.state, niche_label, func.count(Lead.id))
        .outerjoin(
            ListingObservation,
            ListingObservation.id == Lead.current_listing_observation_id,
        )
        .outerjoin(
            SourceNicheMapping,
            and_(
                SourceNicheMapping.segment_key == ListingObservation.source,
                SourceNicheMapping.denied_at.is_(None),
            ),
        )
        .outerjoin(NicheAssignment, NicheAssignment.phone == Lead.phone)
        .where(
            Lead.suppressed.is_(False),
            ~_actively_held(),
            ~_globally_excluded(),
        )
        .group_by(Lead.state, niche_label)
        .order_by(Lead.state, niche_label)
    ).all()
    return [
        {"state": state, "niche": niche_name, "count": int(count)}
        for state, niche_name, count in rows
    ]


def read_pool_breakdown(session: Session) -> dict:
    row = session.get(PoolBreakdownSnapshot, _POOL_BREAKDOWN_ID)
    if row is None:
        return {"asOf": None, "cells": []}
    return {"asOf": _as_of_iso(row.computed_at), "cells": list(row.cells)}


def refresh_pool_breakdown(session: Session) -> dict:
    cells = compute_pool_breakdown(session)
    computed_at = utcnow()
    session.merge(
        PoolBreakdownSnapshot(
            id=_POOL_BREAKDOWN_ID,
            cells=cells,
            computed_at=computed_at,
        )
    )
    session.flush()
    return {"asOf": _as_of_iso(computed_at), "cells": cells}


def _projection_request(customer: Customer) -> LeadRequest:
    return LeadRequest(
        agent=customer,
        agent_id=customer.id,
        user_id=uuid.uuid4(),
        lead_count=1,
        states_snapshot=list(customer.licensed_states or []),
        state_mode="all_saved",
        delivery_email="availability@invalid",
        status=RequestStatus.approved.value,
    )


def compute_customer_available(
    session: Session,
    customer: Customer,
    settings: Settings,
    *,
    niche_policy_rows: list[dict] | None = None,
    as_of: datetime | None = None,
) -> int:
    if not customer.licensed_states:
        return 0
    return inventory_count(
        session,
        _projection_request(customer),
        settings,
        niche_policy_rows=niche_policy_rows,
        as_of=as_of,
    )


def _candidate_base_query(customer: Customer):
    """Leads that pass every eligibility filter except the Cooldown Window."""
    request = _projection_request(customer)
    previously_sent = exists(
        select(DistributionEvent.id).where(
            DistributionEvent.lead_id == Lead.id,
            _same_recipient_clause(request),
        )
    )
    excluded_phone = exists(
        select(ExclusionPhone.phone)
        .join(
            ExclusionList,
            ExclusionList.id == ExclusionPhone.exclusion_list_id,
        )
        .where(
            ExclusionPhone.phone == Lead.phone,
            or_(
                ExclusionList.global_effective.is_(True),
                ExclusionList.customer_id == customer.id,
            ),
        )
    )
    niche = effective_niche_expression()
    return (
        select(Lead)
        .outerjoin(
            ListingObservation,
            ListingObservation.id == Lead.current_listing_observation_id,
        )
        .outerjoin(
            SourceNicheMapping,
            and_(
                SourceNicheMapping.segment_key == ListingObservation.source,
                SourceNicheMapping.denied_at.is_(None),
            ),
        )
        .outerjoin(NicheAssignment, NicheAssignment.phone == Lead.phone)
        .where(
            Lead.state.in_(list(customer.licensed_states or [])),
            Lead.suppressed.is_(False),
            ~previously_sent,
            ~_actively_held(),
            ~excluded_phone,
            policy_clause(customer.id, niche),
        )
    )


def compute_cooldown_forecast(
    session: Session,
    customer: Customer,
    settings: Settings,
    *,
    as_of: datetime | None = None,
) -> tuple[int, list[dict]]:
    """Eligible today plus newly becoming-eligible counts for the next 14 days."""
    as_of = as_of or utcnow()
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    if not customer.licensed_states:
        empty = [
            {
                "offset": offset,
                "date": (as_of.date() + timedelta(days=offset)).isoformat(),
                "count": 0,
            }
            for offset in range(FORECAST_DAYS + 1)
        ]
        return 0, empty

    cooldown_days = max(1, int(customer.cooldown_window_days or 7))
    # Both halves must read the same instant. Counting "available now" against
    # the wall clock while bucketing the rest against `as_of` puts a Lead
    # whose Cooldown Window lapses between the two reads in both or neither.
    available = compute_customer_available(
        session, customer, settings, as_of=as_of
    )
    cutoff_today = as_of - timedelta(days=cooldown_days)

    counts = {0: available}
    for offset in range(1, FORECAST_DAYS + 1):
        counts[offset] = 0

    last_distributions = session.scalars(
        _candidate_base_query(customer)
        .with_only_columns(Lead.last_distributed_at)
        .order_by(None)
    )
    today = as_of.date()
    for last in last_distributions:
        if last is None:
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last <= cutoff_today:
            # Already eligible today; counted in available.
            continue
        eligible_on = (last + timedelta(days=cooldown_days)).date()
        offset = (eligible_on - today).days
        if 0 <= offset <= FORECAST_DAYS:
            counts[offset] += 1

    forecast = [
        {
            "offset": offset,
            "date": (today + timedelta(days=offset)).isoformat(),
            "count": counts[offset],
        }
        for offset in range(FORECAST_DAYS + 1)
    ]
    return available, forecast


def read_customer_availability(session: Session, customer_id: int) -> dict:
    row = session.get(CustomerAvailabilitySnapshot, customer_id)
    if row is None:
        return {"asOf": None, "available": None, "forecast": None}
    return {
        "asOf": _as_of_iso(row.computed_at),
        "available": int(row.available),
        "forecast": list(row.forecast),
    }


def refresh_customer_availability(
    session: Session,
    customer: Customer,
    settings: Settings,
) -> dict:
    as_of = utcnow()
    available, forecast = compute_cooldown_forecast(
        session, customer, settings, as_of=as_of
    )
    session.merge(
        CustomerAvailabilitySnapshot(
            customer_id=customer.id,
            available=available,
            forecast=forecast,
            computed_at=as_of,
        )
    )
    session.flush()
    return {
        "asOf": _as_of_iso(as_of),
        "available": available,
        "forecast": forecast,
    }


def project_customer_availability(
    session: Session,
    customer: Customer,
    settings: Settings,
    *,
    niche_policy_rows: list[dict],
) -> dict:
    """On-demand draft projection — not cached, but stamped with asOf."""
    as_of = utcnow()
    return {
        "asOf": _as_of_iso(as_of),
        "available": compute_customer_available(
            session,
            customer,
            settings,
            niche_policy_rows=niche_policy_rows,
            as_of=as_of,
        ),
    }


def customer_availability_preview(session: Session, customer_id: int | None) -> dict | None:
    """Cached availability figure for exclusion confirmation surfaces."""
    if customer_id is None:
        return None
    snapshot = read_customer_availability(session, customer_id)
    if snapshot["asOf"] is None:
        return None
    return {"available": snapshot["available"], "asOf": snapshot["asOf"]}
