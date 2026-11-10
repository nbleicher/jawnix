"""Lead lookup: everything about one phone number, read on demand.

A phone is unique in the inventory and every record about a Lead is keyed by
its id, so one lookup is a handful of indexed reads and runs in the request.
Nothing here counts the inventory.

"Usable" is the inventory rule (Q1): not suppressed, no active Eligibility
Hold, not globally excluded. "Who can receive it next" applies each active
Customer's own rules on top: permanent no-repeat history (their own and their
Agency's), their database and appointed states, their Exclusion Lists, the
Cooldown Window, and finally the allocation query itself, so "Now" means
exactly what allocation would do.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from sqlalchemy import exists, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .allocation import DEACTIVATED_RECIPIENT_HOLD_DAYS, _same_recipient_clause, eligible_query
from .config import Settings
from .dispositions import CATALOG
from .eligibility import (
    _EVIDENCE_LABELS,
    _HOLD_REASONS,
    _REPORT_REASONS,
    CORRECT_LEAD,
    REMOVE_CORRECTION,
    RESTORE_LEAD,
    RESTORE_NOTICE,
    SUPPRESS_LEAD,
    LeadEvidence,
    lead_evidence,
)
from .lead_databases import db2_inventory
from .models import (
    Agency,
    Customer,
    DatasetPublication,
    DistributionEvent,
    EligibilityHold,
    ExclusionList,
    ExclusionPhone,
    Lead,
    LeadCorrectionEvent,
    LeadDatabaseMembership,
    LeadDispositionState,
    LeadReport,
    LeadRequest,
    LeadUpload,
    ListingObservation,
    ManagedAdmin,
    NicheAssignment,
    SourceNicheMapping,
    utcnow,
)
from .niche_policy import effective_niche_expression
from .pool_analytics import _projection_request
from .states import normalize_phone

log = logging.getLogger(__name__)

LISTING_LIMIT = 50
_PHONE_SHAPE = re.compile(r"^[\d\s().+\-]+$")
_DISPOSITION_LABELS = {
    item.disposition: item.label[:1] + item.label[1:].lower() for item in CATALOG
}

T = TypeVar("T")


def parse_phone(query: str | None) -> str | None:
    """A query that is only a US phone number, as its ten digits."""
    text = (query or "").strip()
    if not text or not _PHONE_SHAPE.match(text):
        return None
    return normalize_phone(text)


def format_phone(phone: str) -> str:
    """"6155550142" reads as "(615) 555-0142"."""
    if len(phone) != 10 or not phone.isdigit():
        return phone
    return f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"


def lookup_href(phone: str) -> str:
    return f"/app/admin/inventory/lookup?phone={phone}"


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _guarded(db: Session, name: str, read: Callable[[], T], fallback: T) -> T:
    """Run a read that touches the DB2 link inside a savepoint.

    DB2 listings live in a foreign table that a development database may not
    have. A failed read rolls back only its savepoint, so the rest of the
    lookup still answers.
    """
    try:
        with db.begin_nested():
            return read()
    except SQLAlchemyError:
        log.warning("Lead lookup could not read %s", name, exc_info=True)
        return fallback


def find_lead(db: Session, *, phone: str | None = None, lead_id: int | None = None) -> Lead | None:
    if lead_id is not None:
        return db.get(Lead, lead_id)
    if phone:
        return db.scalar(select(Lead).where(Lead.phone == phone))
    return None


def uploader_names(db: Session, uploads: list[LeadUpload]) -> dict[uuid.UUID, str]:
    """Uploader names by id for earlier managed administrator uploads.

    Managed administration is retired, but their uploads keep naming them.
    The operator's own uploads have no manager_id.
    """
    manager_ids = {item.manager_id for item in uploads if item.manager_id}
    if not manager_ids:
        return {}
    return {
        manager.auth_user_id: manager.name
        for manager in db.scalars(
            select(ManagedAdmin).where(ManagedAdmin.auth_user_id.in_(manager_ids))
        )
    }


def uploader_name(names: dict[uuid.UUID, str], upload: LeadUpload) -> str:
    if upload.manager_id is None:
        return "Operator"
    return names.get(upload.manager_id, "Managed administrator")


def _effective_niche(db: Session, lead: Lead) -> str:
    """The Niche allocation applies: a source mapping, else a manual assignment."""
    niche = db.scalar(
        select(effective_niche_expression())
        .select_from(Lead)
        .outerjoin(
            ListingObservation,
            ListingObservation.id == Lead.current_listing_observation_id,
        )
        .outerjoin(
            SourceNicheMapping,
            (SourceNicheMapping.segment_key == ListingObservation.source)
            & SourceNicheMapping.denied_at.is_(None),
        )
        .outerjoin(NicheAssignment, NicheAssignment.phone == Lead.phone)
        .where(Lead.id == lead.id)
    )
    return niche or ""


def _exclusions(db: Session, phone: str) -> list[dict[str, object]]:
    rows = db.execute(
        select(ExclusionList, Customer.name)
        .join(ExclusionPhone, ExclusionPhone.exclusion_list_id == ExclusionList.id)
        .outerjoin(Customer, Customer.id == ExclusionList.customer_id)
        .where(ExclusionPhone.phone == phone)
        .order_by(ExclusionList.created_at.desc())
    ).all()
    return [
        {
            "id": str(item.id),
            "filename": item.filename,
            "type": item.exclusion_type,
            "status": item.status,
            "global": item.global_effective,
            "customerId": item.customer_id,
            "customerName": customer_name or "Operator",
            "createdAt": _utc(item.created_at),
        }
        for item, customer_name in rows
    ]


def _db2_listings(
    db: Session, lead: Lead, uploads: dict[uuid.UUID, LeadUpload]
) -> list[dict[str, object]]:
    """Every published DB2 row for this Lead, one per upload.

    The upload ids are listed so the remote primary key (upload, lead) serves
    the read instead of a scan of every DB2 row.
    """
    if not uploads:
        return []
    names = uploader_names(db, list(uploads.values()))
    rows = db.execute(
        select(db2_inventory.c.upload_id, db2_inventory.c.title, db2_inventory.c.state).where(
            db2_inventory.c.upload_id.in_(list(uploads)),
            db2_inventory.c.lead_id == lead.id,
        )
    ).all()
    listings = []
    for upload_id, title, state in rows:
        upload = uploads.get(upload_id)
        if upload is None:
            continue
        listings.append(
            {
                "database": "db2",
                "observedAt": _utc(upload.completed_at or upload.created_at),
                "source": f"DB2 · {upload.filename} ({uploader_name(names, upload)})",
                "title": title,
                "state": state,
                "valid": True,
            }
        )
    return listings


def _db1_listings(db: Session, lead: Lead) -> list[dict[str, object]]:
    observations = list(
        db.scalars(
            select(ListingObservation)
            .where(ListingObservation.lead_id == lead.id)
            .order_by(ListingObservation.observed_at.desc(), ListingObservation.row_number.desc())
            .limit(LISTING_LIMIT)
        )
    )
    checksums = {item.dataset_checksum for item in observations}
    runs = (
        dict(
            db.execute(
                select(DatasetPublication.checksum, DatasetPublication.scraper_run_id).where(
                    DatasetPublication.checksum.in_(checksums)
                )
            ).all()
        )
        if checksums
        else {}
    )
    listings = []
    for item in observations:
        state, _, keyword = item.source.partition("::")
        segment = f"{keyword} · {state}" if keyword else item.source
        run = runs.get(item.dataset_checksum)
        source = " · ".join(part for part in (f"Run {run}" if run else "", segment) if part)
        listings.append(
            {
                "database": "db1",
                "observedAt": _utc(item.observed_at),
                "source": source or "Google Maps",
                "title": item.title,
                "state": item.state,
                "valid": item.valid,
                "current": item.id == lead.current_listing_observation_id,
            }
        )
    return listings


def _deliveries(
    db: Session, events: list[DistributionEvent]
) -> list[dict[str, object]]:
    if not events:
        return []
    event_ids = [event.id for event in events]
    request_ids = {event.request_id for event in events if event.request_id}
    numbers = (
        dict(
            db.execute(
                select(LeadRequest.id, LeadRequest.number).where(LeadRequest.id.in_(request_ids))
            ).all()
        )
        if request_ids
        else {}
    )
    customer_ids = {event.agent_id for event in events if event.agent_id}
    customers = (
        {
            item.id: item
            for item in db.scalars(select(Customer).where(Customer.id.in_(customer_ids)))
        }
        if customer_ids
        else {}
    )
    dispositions = dict(
        db.execute(
            select(
                LeadDispositionState.distribution_event_id,
                LeadDispositionState.current_disposition,
            ).where(LeadDispositionState.distribution_event_id.in_(event_ids))
        ).all()
    )
    rows = []
    for event in events:
        customer = customers.get(event.agent_id) if event.agent_id else None
        number = numbers.get(event.request_id) if event.request_id else None
        disposition = dispositions.get(event.id)
        rows.append(
            {
                "id": event.id,
                "deliveredAt": _utc(event.delivered_at),
                "customer": (
                    {
                        "id": customer.id,
                        "name": customer.name,
                        "href": f"/app/admin/customers/{customer.id}",
                    }
                    if customer is not None and customer.deleted_at is None
                    else None
                ),
                # As recorded at delivery, so an erased or renamed Customer
                # still reads the way the delivery happened.
                "customerName": event.customer_name or (customer.name if customer else ""),
                "agencyName": event.agency_name,
                "batch": (
                    {"number": number, "href": f"/app/admin/batches/{number}"}
                    if number is not None
                    else None
                ),
                "database": event.inventory_database,
                "title": event.title,
                "state": event.state,
                "disposition": _DISPOSITION_LABELS.get(disposition, disposition)
                if disposition
                else None,
            }
        )
    return rows


def _reports(
    db: Session, events: list[DistributionEvent]
) -> list[dict[str, object]]:
    if not events:
        return []
    by_id = {event.id: event for event in events}
    rows = db.execute(
        select(LeadReport, Customer.name)
        .outerjoin(Customer, Customer.id == LeadReport.customer_id)
        .where(LeadReport.distribution_event_id.in_(list(by_id)))
        .order_by(LeadReport.created_at.desc())
    ).all()
    return [
        {
            "id": str(report.id),
            "href": f"/app/admin/fulfillment/reports/{report.id}",
            "reason": report.reason,
            "reasonLabel": _REPORT_REASONS.get(report.reason, report.reason),
            "status": report.status,
            "createdAt": _utc(report.created_at),
            "customerName": customer_name or by_id[report.distribution_event_id].customer_name,
        }
        for report, customer_name in rows
    ]


def _holds(db: Session, lead: Lead) -> list[dict[str, object]]:
    return [
        {
            "id": str(hold.id),
            "reason": hold.reason,
            "reasonLabel": _HOLD_REASONS.get(hold.reason, hold.reason.replace("_", " ").capitalize()),
            "active": hold.active,
            "createdAt": _utc(hold.created_at),
            "releasedAt": _utc(hold.released_at),
            "reportHref": f"/app/admin/fulfillment/reports/{hold.report_id}",
        }
        for hold in db.scalars(
            select(EligibilityHold)
            .where(EligibilityHold.lead_id == lead.id)
            .order_by(EligibilityHold.created_at.desc())
        )
    ]


def _corrections(db: Session, lead: Lead) -> list[dict[str, object]]:
    return [
        {
            "id": str(item.id),
            "action": item.action,
            "title": item.title,
            "state": item.state,
            "reason": item.reason,
            "createdAt": _utc(item.created_at),
            "basedOnLabel": _EVIDENCE_LABELS.get(item.based_on_kind, "Evidence not recorded"),
            "basedOnTitle": item.based_on_title,
            "basedOnState": item.based_on_state,
            "active": item.id == lead.active_correction_id,
        }
        for item in db.scalars(
            select(LeadCorrectionEvent)
            .where(LeadCorrectionEvent.lead_id == lead.id)
            .order_by(LeadCorrectionEvent.created_at.desc())
        )
    ]


def _next_recipients(
    db: Session,
    settings: Settings,
    lead: Lead,
    *,
    events: list[DistributionEvent],
    membership: LeadDatabaseMembership | None,
    usable: bool,
    now: datetime,
) -> list[dict[str, object]]:
    """Every active Customer, grouped by whether and when they could get this Lead."""
    customers = list(
        db.scalars(
            select(Customer)
            .where(Customer.active.is_(True), Customer.deleted_at.is_(None))
            .order_by(Customer.name, Customer.id)
        )
    )
    if not customers:
        return []
    active_customers = {customer.id for customer in customers}
    active_agencies = set(
        db.scalars(
            select(Agency.id).where(Agency.active.is_(True), Agency.deleted_at.is_(None))
        )
    )
    agencies = {
        agency.id: agency
        for agency in db.scalars(
            select(Agency).where(
                Agency.id.in_({c.agency_id for c in customers if c.agency_id is not None})
            )
        )
    } if any(c.agency_id is not None for c in customers) else {}

    def recipient_active(event: DistributionEvent) -> bool:
        # The same reading of an event's recipient as allocation's
        # ``_recipient_status_clauses``: its Customer, else its Agency.
        if event.agent_id is not None:
            return event.agent_id in active_customers
        return event.agency_id is not None and event.agency_id in active_agencies

    own: dict[int, datetime] = {}
    for event in events:
        if event.agent_id in active_customers:
            delivered = _utc(event.delivered_at)
            if event.agent_id not in own or delivered > own[event.agent_id]:
                own[event.agent_id] = delivered

    excluded_by = set(
        db.scalars(
            select(ExclusionList.customer_id)
            .join(ExclusionPhone, ExclusionPhone.exclusion_list_id == ExclusionList.id)
            .where(
                ExclusionPhone.phone == lead.phone,
                ExclusionList.customer_id.is_not(None),
            )
        )
    )
    last_distributed = _utc(lead.last_distributed_at)

    def cooling_until(customer: Customer) -> datetime | None:
        days = max(1, int(customer.cooldown_window_days or 7))
        # Allocation only looks for a cooling event when the Lead itself was
        # distributed inside the longer of the two windows.
        horizon = now - timedelta(days=max(days, DEACTIVATED_RECIPIENT_HOLD_DAYS))
        if last_distributed is None or last_distributed <= horizon:
            return None
        until = None
        for event in events:
            hold = days if recipient_active(event) else DEACTIVATED_RECIPIENT_HOLD_DAYS
            ends = _utc(event.delivered_at) + timedelta(days=hold)
            if ends > now and (until is None or ends > until):
                until = ends
        return until

    def shared_history(customer: Customer) -> bool:
        return bool(
            db.scalar(
                select(
                    exists().where(
                        DistributionEvent.lead_id == lead.id,
                        _same_recipient_clause(_projection_request(customer)),
                    )
                )
            )
        )

    def in_database(customer: Customer) -> bool:
        return membership is not None if customer.lead_database == "db2" else lead.in_db1

    def state_for(customer: Customer) -> str:
        if customer.lead_database == "db2" and membership is not None:
            # A Lead Correction outranks the DB2 row's state (inventory_state).
            return lead.state if lead.active_correction_id else membership.state
        return lead.state

    def allocatable(customer: Customer) -> bool:
        query = (
            eligible_query(_projection_request(customer), settings, as_of=now)
            .with_only_columns(Lead.id)
            .order_by(None)
            .where(Lead.id == lead.id)
        )
        return bool(db.scalar(select(query.exists())))

    delivered: list[dict] = []
    shared: dict[object, list[Customer]] = {}
    grouped: dict[tuple, dict] = {}

    def add(key: tuple, row: dict, customer: Customer) -> None:
        entry = grouped.setdefault(key, {**row, "count": 0, "customers": []})
        entry["count"] += 1
        entry["customers"].append(customer.name)

    for customer in customers:
        if customer.id in own:
            delivered.append(
                {
                    "key": f"customer:{customer.id}",
                    "label": customer.name,
                    "href": f"/app/admin/customers/{customer.id}",
                    "status": "never",
                    "reason": "delivered",
                    "at": own[customer.id],
                    "count": 1,
                    "customers": [customer.name],
                }
            )
            continue
        if events and shared_history(customer):
            shared.setdefault(customer.agency_id or f"customer:{customer.id}", []).append(customer)
            continue
        if not usable:
            add(("not_usable",), {"key": "not_usable", "label": "All other customers", "status": "blocked", "reason": "not_usable"}, customer)
            continue
        database = customer.lead_database or "db1"
        label = "DB2" if database == "db2" else "DB1"
        if not in_database(customer):
            add(
                ("not_in_database", database),
                {"key": f"not_in_database:{database}", "label": f"{label} customers", "status": "blocked", "reason": "not_in_database", "database": database},
                customer,
            )
            continue
        state = state_for(customer)
        if state not in set(customer.licensed_states or []):
            add(
                ("not_appointed", state),
                {"key": f"not_appointed:{state}", "label": f"Customers not appointed in {state or 'its state'}", "status": "blocked", "reason": "not_appointed"},
                customer,
            )
            continue
        if customer.id in excluded_by:
            add(
                ("customer_exclusion",),
                {"key": "customer_exclusion", "label": "Customers who excluded it", "status": "blocked", "reason": "customer_exclusion"},
                customer,
            )
            continue
        until = cooling_until(customer)
        if until is not None:
            days = max(1, int(customer.cooldown_window_days or 7))
            add(
                ("cooldown", days, until.isoformat()),
                {"key": f"cooldown:{days}:{until.isoformat()}", "label": f"Customers with {days}-day cooldown", "status": "later", "reason": "cooldown", "at": until},
                customer,
            )
            continue
        if allocatable(customer):
            add(("now",), {"key": "now", "label": "Customers who can receive it now", "status": "now", "reason": "eligible"}, customer)
        else:
            add(
                ("niche_policy",),
                {"key": "niche_policy", "label": "Customers whose Niche policy leaves it out", "status": "blocked", "reason": "niche_policy"},
                customer,
            )

    delivered.sort(key=lambda row: row["at"], reverse=True)
    rows = list(delivered)
    for key, members in shared.items():
        agency = agencies.get(key) if isinstance(key, int) else None
        if agency is not None:
            rows.append(
                {
                    "key": f"agency:{agency.id}",
                    "label": f"{agency.name} members",
                    "href": f"/app/admin/agencies/{agency.id}",
                    "status": "never",
                    "reason": "shared_history",
                    "count": len(members),
                    "customers": [member.name for member in members],
                }
            )
        else:
            for member in members:
                rows.append(
                    {
                        "key": f"customer:{member.id}",
                        "label": member.name,
                        "href": f"/app/admin/customers/{member.id}",
                        "status": "never",
                        "reason": "shared_history",
                        "count": 1,
                        "customers": [member.name],
                    }
                )
    order = {"now": 0, "later": 1, "blocked": 2}
    rows.extend(
        sorted(
            grouped.values(),
            key=lambda row: (order.get(str(row["status"]), 3), str(row.get("at") or ""), str(row["key"])),
        )
    )
    return rows


def lead_lookup(
    db: Session,
    settings: Settings,
    *,
    phone: str | None = None,
    lead_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """One phone number (or Lead id): identity, usability, history, and controls."""
    now = _utc(now) or utcnow()
    normalized = parse_phone(phone) if phone is not None else None
    lead = find_lead(db, phone=normalized, lead_id=lead_id)
    if lead is None:
        return {
            "query": phone if phone is not None else str(lead_id or ""),
            "phone": normalized,
            "valid": normalized is not None or lead_id is not None,
            "lead": None,
            "exclusions": _exclusions(db, normalized) if normalized else [],
        }

    membership = db.get(LeadDatabaseMembership, lead.id)
    events = list(
        db.scalars(
            select(DistributionEvent)
            .where(DistributionEvent.lead_id == lead.id)
            .order_by(DistributionEvent.delivered_at.desc(), DistributionEvent.id.desc())
        )
    )
    holds = _holds(db, lead)
    exclusions = _exclusions(db, lead.phone)
    active_holds = [hold for hold in holds if hold["active"]]
    global_exclusions = [item for item in exclusions if item["global"]]
    reasons = []
    if lead.suppressed:
        reasons.append({"kind": "suppressed", "label": "Suppressed", "detail": lead.suppression_reason})
    for hold in active_holds:
        reasons.append({"kind": "held", "label": "On an Eligibility Hold", "detail": hold["reasonLabel"]})
    for item in global_exclusions:
        reasons.append({"kind": "excluded", "label": "Globally excluded", "detail": item["filename"]})
    usable = not reasons

    complete_uploads = {
        upload.id: upload
        for upload in db.scalars(select(LeadUpload).where(LeadUpload.status == "complete"))
    } if membership is not None else {}
    db2_listings = _guarded(
        db, "DB2 listings", lambda: _db2_listings(db, lead, complete_uploads), None
    )
    upload = db.get(LeadUpload, membership.upload_id) if membership is not None else None
    if db2_listings is None:
        # Without the DB2 link, the membership still says which upload the
        # Lead came from; its title stays in DB2.
        db2_listings = (
            [
                {
                    "database": "db2",
                    "observedAt": _utc(upload.completed_at or upload.created_at),
                    "source": f"DB2 · {upload.filename} ({uploader_name(uploader_names(db, [upload]), upload)})",
                    "title": "",
                    "state": membership.state,
                    "valid": True,
                }
            ]
            if upload is not None and membership is not None
            else []
        )
    listings = [*_db1_listings(db, lead), *db2_listings]
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    listings.sort(key=lambda row: row["observedAt"] or epoch, reverse=True)

    evidence = _guarded(
        db,
        "Lead evidence",
        lambda: lead_evidence(db, lead),
        LeadEvidence(kind="none", label=_EVIDENCE_LABELS["none"], title="", state=""),
    )
    title = lead.title if lead.in_db1 or lead.active_correction_id else evidence.title
    state = lead.state if lead.in_db1 or lead.active_correction_id else (evidence.state or (membership.state if membership else ""))

    actions = [CORRECT_LEAD]
    if lead.active_correction_id is not None:
        actions.append(REMOVE_CORRECTION)
    actions.append(RESTORE_LEAD if lead.suppressed else SUPPRESS_LEAD)

    return {
        "query": phone if phone is not None else str(lead_id),
        "phone": lead.phone,
        "valid": True,
        "lead": {
            "id": lead.id,
            "phone": lead.phone,
            "title": title,
            "state": state,
            "niche": _effective_niche(db, lead),
            "firstSeenAt": _utc(lead.first_seen_at),
            "lastDeliveredAt": _utc(lead.last_distributed_at),
        },
        "usable": {"usable": usable, "reasons": reasons},
        "databases": {
            "db1": {
                "member": bool(lead.in_db1),
                "source": (
                    "Google Maps listing"
                    if lead.current_listing_observation_id is not None
                    else "Imported listing"
                    if lead.in_db1
                    else ""
                ),
            },
            "db2": {
                "member": membership is not None,
                "state": membership.state if membership is not None else "",
                "upload": (
                    {
                        "id": str(upload.id),
                        "filename": upload.filename,
                        "uploadedBy": uploader_name(uploader_names(db, [upload]), upload),
                        "at": _utc(upload.completed_at or upload.created_at),
                    }
                    if upload is not None
                    else None
                ),
            },
        },
        "deliveries": _deliveries(db, events),
        "listings": listings,
        "reports": _reports(db, events),
        "holds": holds,
        "suppression": {
            "suppressed": bool(lead.suppressed),
            "reason": lead.suppression_reason,
            "restoreNotice": RESTORE_NOTICE,
        },
        "corrections": _corrections(db, lead),
        "exclusions": exclusions,
        "nextRecipients": _next_recipients(
            db,
            settings,
            lead,
            events=events,
            membership=membership,
            usable=usable,
            now=now,
        ),
        "evidence": evidence.as_dict(),
        "actions": [action.as_dict() for action in actions],
    }
