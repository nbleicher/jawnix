"""Agency assignment and permanent no-repeat history.

Current membership answers where a Customer belongs today. Permanent history
keys answer which Customers and Agencies have ever shared no-repeat history.
Assignment merges those keys transitively and never splits them; immutable
Distribution Events continue to point at their original snapshots.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .activity import record_activity
from .config import Settings
from .models import (
    Agency,
    AgencyMembershipHistory,
    AuditEntry,
    Customer,
    DistributionEvent,
    EligibilityHold,
    Lead,
    SharedHistoryLeadCount,
    utcnow,
)


@dataclass(frozen=True)
class HistorySubjects:
    keys: frozenset[str]
    customer_ids: frozenset[int]
    agency_ids: frozenset[int]


class AgencyAssignmentConflict(ValueError):
    pass


_AGENCY_ACTIVITY_LABELS = {
    "agency_created": "Agency created",
    "agency_updated": "Agency updated",
    "agency_deleted": "Agency removed from active use",
    "agency_hard_deleted": "Agency permanently deleted",
    "agency_hard_delete_refused": "Permanent deletion refused",
}


def _history_subjects(
    db: Session,
    *,
    keys: set[str],
) -> HistorySubjects:
    """Resolve the permanent group plus any unmaterialized current edges.

    Production rows are aligned by the migration and every assignment. The
    current-edge expansion also makes old fixtures/imports safe if they create
    a relationship without going through the administrator assignment route.
    """
    resolved = set(keys)
    customer_ids: set[int] = set()
    agency_ids: set[int] = set()
    while True:
        customers = list(
            db.scalars(
                select(Customer).where(
                    Customer.permanent_history_key.in_(resolved)
                )
            )
        )
        agencies = list(
            db.scalars(
                select(Agency).where(
                    Agency.permanent_history_key.in_(resolved)
                )
            )
        )
        next_keys = {
            item.permanent_history_key for item in (*customers, *agencies)
        }
        next_customer_ids = {item.id for item in customers}
        next_agency_ids = {item.id for item in agencies}

        current_agency_ids = {
            item.agency_id
            for item in customers
            if item.agency_id is not None
        }
        if current_agency_ids:
            current_agencies = list(
                db.scalars(
                    select(Agency).where(Agency.id.in_(current_agency_ids))
                )
            )
            next_keys.update(
                item.permanent_history_key for item in current_agencies
            )
            next_agency_ids.update(item.id for item in current_agencies)

        if next_agency_ids:
            current_members = list(
                db.scalars(
                    select(Customer).where(
                        Customer.agency_id.in_(next_agency_ids)
                    )
                )
            )
            next_keys.update(
                item.permanent_history_key for item in current_members
            )
            next_customer_ids.update(item.id for item in current_members)

        changed = (
            not next_keys.issubset(resolved)
            or not next_customer_ids.issubset(customer_ids)
            or not next_agency_ids.issubset(agency_ids)
        )
        resolved.update(next_keys)
        customer_ids.update(next_customer_ids)
        agency_ids.update(next_agency_ids)
        if not changed:
            break
    return HistorySubjects(
        keys=frozenset(resolved),
        customer_ids=frozenset(customer_ids),
        agency_ids=frozenset(agency_ids),
    )


def history_for_customer(db: Session, customer: Customer) -> HistorySubjects:
    keys = {customer.permanent_history_key}
    if customer.agency is not None:
        keys.add(customer.agency.permanent_history_key)
    return _history_subjects(db, keys=keys)


def history_for_agency(db: Session, agency: Agency) -> HistorySubjects:
    return _history_subjects(
        db,
        keys={agency.permanent_history_key},
    )


def _history_summary(
    db: Session,
    *,
    subjects: HistorySubjects,
) -> dict:
    return {
        "customers": len(subjects.customer_ids),
        "agencies": len(subjects.agency_ids),
        "distributedLeads": _history_lead_count(db, subjects),
    }


# The distributed-Lead count behind an Agency card only changes on batch
# delivery or a history merge, but computing it walks millions of index
# entries (~30s cold in production). Counts at production scale are served
# from a persisted row and refreshed in the background once stale, so no
# request ever waits on the recount; below the threshold the count is always
# computed live, which keeps tests and small deployments exact. A merge
# changes the subject sets and therefore the key, so a merged Agency never
# reads the pre-merge entry.
_HISTORY_LEAD_COUNT_CACHE_THRESHOLD = 100_000
_HISTORY_LEAD_COUNT_TTL = timedelta(hours=1)
_history_lead_refreshing: set[str] = set()
_history_lead_refresh_lock = threading.Lock()


def _history_subject_key(subjects: HistorySubjects) -> str:
    customers = ",".join(str(id) for id in sorted(subjects.customer_ids))
    agencies = ",".join(str(id) for id in sorted(subjects.agency_ids))
    return f"customers:{customers}|agencies:{agencies}"


def _count_history_leads(db: Session, subjects: HistorySubjects) -> int:
    # Counted in SQL: materializing the ids (as assignment_preview must for
    # its set algebra) ships millions of rows per request. The UNION of the
    # two single-column predicates runs both branches as index-only scans.
    branches = []
    if subjects.agency_ids:
        branches.append(
            select(DistributionEvent.lead_id).where(
                DistributionEvent.agency_id.in_(subjects.agency_ids)
            )
        )
    if subjects.customer_ids:
        branches.append(
            select(DistributionEvent.lead_id).where(
                DistributionEvent.agent_id.in_(subjects.customer_ids)
            )
        )
    if not branches:
        return 0
    leads = (
        branches[0].union(*branches[1:])
        if len(branches) > 1
        else branches[0].distinct()
    )
    return int(
        db.scalar(select(func.count()).select_from(leads.subquery())) or 0
    )


def _store_history_lead_count(
    db: Session, *, key: str, count: int
) -> None:
    db.merge(
        SharedHistoryLeadCount(
            subject_key=key,
            lead_count=count,
            computed_at=utcnow(),
        )
    )


def _refresh_history_lead_count(key: str, subjects: HistorySubjects) -> None:
    from .database import SessionLocal

    with _history_lead_refresh_lock:
        if key in _history_lead_refreshing:
            return
        _history_lead_refreshing.add(key)

    def refresh() -> None:
        try:
            with SessionLocal() as session:
                count = _count_history_leads(session, subjects)
                _store_history_lead_count(session, key=key, count=count)
                session.commit()
        except Exception:  # pragma: no cover - best-effort refresh
            pass
        finally:
            with _history_lead_refresh_lock:
                _history_lead_refreshing.discard(key)

    threading.Thread(
        target=refresh,
        daemon=True,
        name=f"history-lead-count-refresh:{key}",
    ).start()


def _history_lead_count(db: Session, subjects: HistorySubjects) -> int:
    key = _history_subject_key(subjects)
    row = db.get(SharedHistoryLeadCount, key)
    if (
        row is not None
        and row.lead_count >= _HISTORY_LEAD_COUNT_CACHE_THRESHOLD
    ):
        computed_at = row.computed_at
        if computed_at.tzinfo is None:
            computed_at = computed_at.replace(tzinfo=timezone.utc)
        if utcnow() - computed_at >= _HISTORY_LEAD_COUNT_TTL:
            _refresh_history_lead_count(key, subjects)
        return row.lead_count

    count = _count_history_leads(db, subjects)
    if count >= _HISTORY_LEAD_COUNT_CACHE_THRESHOLD:
        _store_history_lead_count(db, key=key, count=count)
        db.commit()
    return count


def _history_lead_clauses(subjects: HistorySubjects) -> list:
    clauses = []
    if subjects.customer_ids:
        clauses.append(
            DistributionEvent.agent_id.in_(subjects.customer_ids)
        )
    if subjects.agency_ids:
        clauses.append(
            DistributionEvent.agency_id.in_(subjects.agency_ids)
        )
    return clauses


def agency_directory(
    db: Session,
    *,
    query: str = "",
    status: str = "all",
) -> dict:
    """Build the administrator's searchable Agency read model."""
    term = query.strip()
    if status not in {"all", "active", "deactivated"}:
        status = "all"
    selection = select(Agency).where(Agency.deleted_at.is_(None))
    if status == "active":
        selection = selection.where(Agency.active.is_(True))
    elif status == "deactivated":
        selection = selection.where(Agency.active.is_(False))

    # The whole Customer population loads in one query and the search term
    # matches in Python: a member hit must surface its parent Agency with the
    # full hierarchy intact, which a SQL LIKE on Agency columns cannot do.
    customers = list(
        db.scalars(
            select(Customer)
            .where(Customer.deleted_at.is_(None))
            .order_by(Customer.name, Customer.id)
        )
    )
    members_by_agency: dict[int | None, list[Customer]] = {}
    for customer in customers:
        members_by_agency.setdefault(customer.agency_id, []).append(customer)

    normalized_term = term.lower()

    def matches(value: str) -> bool:
        return not normalized_term or normalized_term in value.lower()

    def status_allows(member: Customer) -> bool:
        return (
            status == "all"
            or (status == "active" and member.active)
            or (status == "deactivated" and not member.active)
        )

    agencies = [
        agency
        for agency in db.scalars(
            selection.order_by(Agency.name, Agency.id)
        )
        if matches(agency.name)
        or matches(agency.slug)
        or any(
            matches(member.name) or matches(member.slug)
            for member in members_by_agency.get(agency.id, [])
        )
    ]

    def serialize_member(member: Customer) -> dict:
        return {
            "id": member.id,
            "slug": member.slug,
            "name": member.name,
            "active": member.active,
            "licensedStates": list(member.licensed_states or []),
            "href": f"/app/admin/customers/{member.id}",
        }

    rows = []
    for agency in agencies:
        members = members_by_agency.get(agency.id, [])
        subjects = history_for_agency(db, agency)
        agency_activity_at = db.scalar(
            select(func.max(AuditEntry.created_at)).where(
                AuditEntry.target_type == "agency",
                AuditEntry.target_id == str(agency.id),
            )
        )
        membership_activity_at = db.scalar(
            select(func.max(AgencyMembershipHistory.started_at)).where(
                AgencyMembershipHistory.agency_id == agency.id
            )
        )
        rows.append(
            {
                "id": agency.id,
                "slug": agency.slug,
                "name": agency.name,
                "active": agency.active,
                "status": {
                    "label": "Active" if agency.active else "Deactivated",
                    "description": (
                        "Customers may be assigned to this Agency."
                        if agency.active
                        else "New assignments and Customer work are blocked."
                    ),
                    "tone": "success" if agency.active else "warning",
                },
                "members": [serialize_member(member) for member in members],
                "currentMembers": len(members),
                "sharedHistory": _history_summary(
                    db,
                    subjects=subjects,
                ),
                "lastActivityAt": max(
                    (
                        value
                        for value in (
                            agency_activity_at,
                            membership_activity_at,
                        )
                        if value is not None
                    ),
                    default=None,
                ),
                "href": f"/app/admin/agencies/{agency.id}",
            }
        )
    total = int(
        db.scalar(
            select(func.count(Agency.id)).where(
                Agency.deleted_at.is_(None)
            )
        )
        or 0
    )
    return {
        "filters": {"query": term, "status": status},
        "total": total,
        "matched": len(rows),
        "agencies": rows,
        "independent": [
            serialize_member(member)
            for member in members_by_agency.get(None, [])
            if (matches(member.name) or matches(member.slug))
            and status_allows(member)
        ],
    }


def _agency_activity(
    db: Session,
    *,
    agency: Agency,
    limit: int = 30,
) -> list[dict]:
    activity = [
        {
            "id": str(entry.id),
            "action": entry.action,
            "label": _AGENCY_ACTIVITY_LABELS.get(
                entry.action,
                entry.action.replace("_", " ").capitalize(),
            ),
            "actor": entry.actor_user_id,
            "reason": entry.reason,
            "createdAt": entry.created_at,
        }
        for entry in db.scalars(
            select(AuditEntry)
            .where(
                AuditEntry.target_type == "agency",
                AuditEntry.target_id == str(agency.id),
            )
            .order_by(AuditEntry.created_at.desc(), AuditEntry.id)
            .limit(limit)
        )
    ]
    memberships = list(
        db.scalars(
            select(AgencyMembershipHistory)
            .where(AgencyMembershipHistory.agency_id == agency.id)
            .order_by(
                AgencyMembershipHistory.started_at.desc(),
                AgencyMembershipHistory.id.desc(),
            )
            .limit(limit)
        )
    )
    customers_by_id = {
        customer.id: customer
        for customer in db.scalars(
            select(Customer).where(
                Customer.id.in_(
                    {
                        membership.customer_id
                        for membership in memberships
                    }
                )
            )
        )
    }
    for membership in memberships:
        customer = db.get(Customer, membership.customer_id)
        activity.append(
            {
                "id": f"membership-{membership.id}",
                "action": "customer_assigned",
                "label": (
                    f"{customer.name if customer else 'Customer'} assigned"
                ),
                "actor": membership.assigned_by,
                "reason": membership.reason,
                "createdAt": membership.started_at,
            }
        )
    activity.sort(key=lambda item: item["createdAt"], reverse=True)
    return activity[:limit]


def agency_details(
    db: Session,
    *,
    agency: Agency,
) -> dict:
    """Build one Agency without turning it into a sign-in identity."""
    subjects = history_for_agency(db, agency)
    members = list(
        db.scalars(
            select(Customer)
            .where(
                Customer.agency_id == agency.id,
                Customer.deleted_at.is_(None),
            )
            .order_by(Customer.name, Customer.id)
        )
    )
    memberships = list(
        db.scalars(
            select(AgencyMembershipHistory)
            .where(AgencyMembershipHistory.agency_id == agency.id)
            .order_by(
                AgencyMembershipHistory.started_at.desc(),
                AgencyMembershipHistory.id.desc(),
            )
        )
    )
    distribution_count = int(
        db.scalar(
            select(func.count(DistributionEvent.id)).where(
                DistributionEvent.agency_id == agency.id
            )
        )
        or 0
    )
    dependencies = {
        "customers": len(members),
        "distributions": distribution_count,
        "membershipHistory": len(memberships),
    }
    return {
        "agency": {
            "id": agency.id,
            "slug": agency.slug,
            "name": agency.name,
            "active": agency.active,
            "status": {
                "label": "Active" if agency.active else "Deactivated",
                "description": (
                    "Customers may be assigned to this Agency."
                    if agency.active
                    else "New assignments and Customer work are blocked."
                ),
                "tone": "success" if agency.active else "warning",
            },
        },
        "members": [
            {
                "id": member.id,
                "slug": member.slug,
                "name": member.name,
                "active": member.active,
                "licensedStates": list(member.licensed_states or []),
                "href": f"/app/admin/customers/{member.id}",
            }
            for member in members
        ],
        "sharedHistory": _history_summary(db, subjects=subjects),
        "membershipHistory": [
            {
                "id": membership.id,
                "customerId": membership.customer_id,
                "customer": (
                    customers_by_id[membership.customer_id].name
                    if membership.customer_id in customers_by_id
                    else "Deleted Customer"
                ),
                "startedAt": membership.started_at,
                "endedAt": membership.ended_at,
                "assignedBy": membership.assigned_by,
                "reason": membership.reason,
            }
            for membership in memberships
        ],
        "activity": _agency_activity(db, agency=agency),
        "deletion": {
            "dependencies": dependencies,
            "requiresDeactivation": agency.active,
            "canHardDelete": (
                not agency.active and not any(dependencies.values())
            ),
        },
    }


def _history_lead_ids(
    db: Session,
    subjects: HistorySubjects,
) -> set[int]:
    clauses = _history_lead_clauses(subjects)
    if not clauses:
        return set()
    return set(
        db.scalars(
            select(DistributionEvent.lead_id)
            .where(or_(*clauses))
            .distinct()
        )
    )


def _eligible_inventory_count(
    db: Session,
    *,
    blocked_lead_ids: set[int],
    settings: Settings,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.global_cooldown_days
    )
    held = select(EligibilityHold.lead_id).where(
        EligibilityHold.active.is_(True)
    )
    query = select(func.count(Lead.id)).where(
        Lead.suppressed.is_(False),
        or_(
            Lead.last_distributed_at.is_(None),
            Lead.last_distributed_at <= cutoff,
        ),
        ~Lead.id.in_(held),
    )
    if blocked_lead_ids:
        query = query.where(~Lead.id.in_(blocked_lead_ids))
    return int(db.scalar(query) or 0)


def assignment_preview(
    db: Session,
    *,
    customer: Customer,
    destination: Agency | None,
    settings: Settings,
) -> dict:
    source = history_for_customer(db, customer)
    target = (
        history_for_agency(db, destination)
        if destination is not None
        else HistorySubjects(frozenset(), frozenset(), frozenset())
    )
    source_leads = _history_lead_ids(db, source)
    target_leads = _history_lead_ids(db, target)
    combined_leads = source_leads | target_leads
    return {
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "agencyId": customer.agency_id,
            "agency": (
                customer.agency.name if customer.agency is not None else ""
            ),
        },
        "destination": (
            {
                "id": destination.id,
                "name": destination.name,
                "active": destination.active,
                "currentMembers": len(
                    list(
                        db.scalars(
                            select(Customer.id).where(
                                Customer.agency_id == destination.id,
                                Customer.id != customer.id,
                                Customer.deleted_at.is_(None),
                            )
                        )
                    )
                ),
            }
            if destination is not None
            else None
        ),
        "inventory": {
            "eligibleBefore": _eligible_inventory_count(
                db,
                blocked_lead_ids=source_leads,
                settings=settings,
            ),
            "eligibleAfter": _eligible_inventory_count(
                db,
                blocked_lead_ids=combined_leads,
                settings=settings,
            ),
        },
        "sharedHistory": {
            "customersAfter": len(source.customer_ids | target.customer_ids),
            "agenciesAfter": len(source.agency_ids | target.agency_ids),
            "distributedLeadsAfter": len(combined_leads),
        },
        "consequences": {
            "customerHistoryBlockedForDestination": len(
                source_leads - target_leads
            ),
            "destinationHistoryBlockedForCustomer": len(
                target_leads - source_leads
            ),
            "historyMergeIsPermanent": destination is not None,
        },
    }


def assign_customer(
    db: Session,
    *,
    customer: Customer,
    destination: Agency | None,
    actor_id: object,
    reason: str,
    confirmed: bool,
    settings: Settings,
    precomputed_preview: dict | None = None,
) -> dict:
    """Assign membership and permanently merge no-repeat history.

    The offline User Account migration supplies its already approved,
    content-addressed impact report through ``precomputed_preview`` so applying
    eight mappings does not rematerialize millions of distributed Lead IDs.
    Interactive callers continue to calculate the live preview here.
    """

    reason = reason.strip()
    if not reason:
        raise AgencyAssignmentConflict(
            "An audit reason is required for an Agency assignment."
        )
    if not confirmed:
        raise AgencyAssignmentConflict(
            "Explicit confirmation is required because history merges "
            "permanently."
        )
    if destination is not None and (
        not destination.active or destination.deleted_at is not None
    ):
        raise AgencyAssignmentConflict(
            "A Customer cannot join a deactivated Agency."
        )

    preview = (
        precomputed_preview
        if precomputed_preview is not None
        else assignment_preview(
            db,
            customer=customer,
            destination=destination,
            settings=settings,
        )
    )
    previous_agency = customer.agency
    if customer.agency_id == (
        destination.id if destination is not None else None
    ):
        return preview

    now = datetime.now(timezone.utc)
    source = history_for_customer(db, customer)
    target = (
        history_for_agency(db, destination)
        if destination is not None
        else HistorySubjects(frozenset(), frozenset(), frozenset())
    )
    merged_keys = set(source.keys | target.keys)
    if destination is not None:
        canonical_key = min(merged_keys)
        for item in db.scalars(
            select(Customer)
            .where(Customer.permanent_history_key.in_(merged_keys))
            .order_by(Customer.id)
            .with_for_update()
        ):
            item.permanent_history_key = canonical_key
        for item in db.scalars(
            select(Agency)
            .where(Agency.permanent_history_key.in_(merged_keys))
            .order_by(Agency.id)
            .with_for_update()
        ):
            item.permanent_history_key = canonical_key

    current_membership = db.scalar(
        select(AgencyMembershipHistory)
        .where(
            AgencyMembershipHistory.customer_id == customer.id,
            AgencyMembershipHistory.ended_at.is_(None),
        )
        .with_for_update()
    )
    if current_membership is None and previous_agency is not None:
        current_membership = AgencyMembershipHistory(
            customer_id=customer.id,
            agency_id=previous_agency.id,
            started_at=now,
            ended_at=now,
            assigned_by="system:existing-membership",
            reason="Existing membership retained before reassignment.",
        )
        db.add(current_membership)
    elif current_membership is not None:
        current_membership.ended_at = now

    customer.agency_id = destination.id if destination is not None else None
    if destination is not None:
        db.add(
            AgencyMembershipHistory(
                customer_id=customer.id,
                agency_id=destination.id,
                started_at=now,
                assigned_by=str(actor_id),
                reason=reason,
            )
        )

    record_activity(
        db,
        action="customer_agency_assignment_changed",
        target_type="customer",
        target_id=customer.id,
        actor_id=actor_id,
        reason=reason,
        details={
            "before": {
                "agencyId": (
                    previous_agency.id
                    if previous_agency is not None
                    else None
                ),
                "agencyName": (
                    previous_agency.name
                    if previous_agency is not None
                    else ""
                ),
            },
            "after": {
                "agencyId": (
                    destination.id if destination is not None else None
                ),
                "agencyName": (
                    destination.name if destination is not None else ""
                ),
            },
            "permanentHistory": preview["consequences"],
        },
    )
    db.flush()
    result = (
        dict(preview)
        if precomputed_preview is not None
        else assignment_preview(
            db,
            customer=customer,
            destination=destination,
            settings=settings,
        )
    )
    result["ok"] = True
    return result
