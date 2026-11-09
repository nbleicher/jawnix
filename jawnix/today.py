"""The operator's Today page: numbers, last night, decisions, batches.

Today replaces the Overview. Its Decisions list merges every item waiting on
the operator into one oldest-first list, using the same rules as the
"Decisions waiting" number (``operator_numbers.waiting_decisions``), so the
list and the number always agree. Each item names the record it is about;
kinds without their own detail endpoint carry the facts their side panel
shows.

Each part is read in its own savepoint, so one failing read leaves the rest
of the page usable, as the Overview did.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .acquisition import _anomalous_segments, _exclusion_dict, niche_dict
from .batches import (
    availability,
    batch_href,
    batch_rows,
    batches_with_databases,
)
from .customer_history import archived_customer_matches
from .customer_registration import registration_status
from .eligibility import _REPORT_REASONS
from .models import (
    BatchArtifact,
    Customer,
    CustomerAvailabilitySnapshot,
    CustomerRegistration,
    DatasetPublication,
    EligibilityHold,
    ExclusionList,
    InventoryConflict,
    InventorySyncAttempt,
    Job,
    JobStatus,
    LeadReport,
    LeadRequest,
    NightlyReview,
    RequestStatus,
    ScrapeAnomaly,
    ScrapeSegmentResult,
    SourceNicheMapping,
)
from .operations_overview import _job_destination
from .operator_numbers import operator_numbers

log = logging.getLogger(__name__)

#: Enough to work a long morning; the number above the list counts them all.
DECISION_LIMIT = 100

#: Batches moving without a decision: approved, generating, waiting.
IN_PROGRESS = frozenset(
    {
        RequestStatus.approved.value,
        RequestStatus.processing.value,
        RequestStatus.waiting_inventory.value,
        RequestStatus.generated.value,
    }
)

_EXCLUSION_TYPES = {
    "landline": "Landline",
    "dnc": "DNC",
    "tcpa_litigator": "TCPA litigator",
}

_ANOMALY_REASONS = {
    "zero_valid_listings": "no valid listings",
    "more_than_50_percent_down": "down more than 50%",
    "more_than_200_percent_up": "up more than 200%",
}

_JOB_LABELS = {
    "allocate_request": "Allocate",
    "deliver_request": "Deliver",
    "notify_request": "Telegram notice",
    "update_notification": "Telegram update",
    "fulfill_round_robin": "Rotation",
    "run_scraper": "Scrape run",
    "sync_inventory": "Inventory sync",
    "notify_nightly_review": "Nightly review notice",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def _states(states: list[str] | None) -> str:
    return ", ".join(states or []) or "No states"


def _segment_label(key: str) -> str:
    """"TX::roofing" reads as "roofing · TX"."""
    state, _, keyword = key.partition("::")
    return f"{keyword} · {state}" if keyword else key


def _decision(
    *,
    kind: str,
    label: str,
    record_id: str,
    subject: str,
    detail: str,
    since: datetime | None,
    href: str | None,
    batch: LeadRequest | None = None,
    facts: dict | None = None,
) -> dict[str, object]:
    return {
        "key": f"{kind}:{record_id}",
        "kind": kind,
        "label": label,
        "recordId": record_id,
        "subject": subject,
        "detail": detail,
        "since": _utc(since),
        "href": href,
        "batch": (
            {"id": str(batch.id), "number": batch.number}
            if batch is not None
            else None
        ),
        "facts": facts,
    }


def _batch_decisions(db: Session) -> list[dict[str, object]]:
    items = list(
        db.scalars(
            select(LeadRequest)
            .options(
                selectinload(LeadRequest.artifact),
                selectinload(LeadRequest.agent),
            )
            .where(
                LeadRequest.status.in_(
                    {RequestStatus.pending.value, RequestStatus.failed.value}
                )
            )
            .order_by(LeadRequest.created_at.asc())
            .limit(DECISION_LIMIT)
        )
    )
    snapshots = {
        row.customer_id: row
        for row in db.scalars(
            select(CustomerAvailabilitySnapshot).where(
                CustomerAvailabilitySnapshot.customer_id.in_(
                    {item.agent_id for item in items}
                )
            )
        )
    } if items else {}
    decisions = []
    for item in items:
        scope = f"{item.lead_count:,} leads · {_states(item.states_snapshot)}"
        if item.status == RequestStatus.pending.value:
            fill = availability(
                item, committed=None, snapshot=snapshots.get(item.agent_id)
            )
            if fill["kind"] == "fillable":
                scope += " · fillable"
            elif fill["kind"] == "short":
                scope += f" · short {int(fill['short']):,}"
            decisions.append(
                _decision(
                    kind="approval",
                    label="Approval",
                    record_id=str(item.id),
                    subject=item.customer.name,
                    detail=scope,
                    since=item.created_at,
                    href=batch_href(item),
                    batch=item,
                )
            )
            continue
        artifact = item.artifact
        if artifact is not None:
            error = (artifact.last_error or "Delivery failed").strip()
            decisions.append(
                _decision(
                    kind="deliveryFailed",
                    label="Delivery failed",
                    record_id=str(item.id),
                    subject=item.customer.name,
                    detail=(
                        f"{error[:80]} · "
                        f"{_plural(artifact.delivery_attempts, 'attempt')}"
                    ),
                    since=item.closed_at or item.created_at,
                    href=batch_href(item),
                    batch=item,
                )
            )
        else:
            decisions.append(
                _decision(
                    kind="failedBatch",
                    label="Failed batch",
                    record_id=str(item.id),
                    subject=item.customer.name,
                    detail=(item.status_message or scope)[:100],
                    since=item.closed_at or item.created_at,
                    href=batch_href(item),
                    batch=item,
                )
            )
    return decisions


def _conflict_decisions(db: Session) -> list[dict[str, object]]:
    decisions = []
    for conflict in db.scalars(
        select(InventoryConflict)
        .where(InventoryConflict.status == "pending")
        .order_by(InventoryConflict.created_at.asc())
        .limit(DECISION_LIMIT)
    ):
        older = db.get(LeadRequest, conflict.older_request_id)
        newer = db.get(LeadRequest, conflict.newer_request_id)
        overlap = len((conflict.inventory_snapshot or {}).get("sharedLeadIds") or [])
        decisions.append(
            _decision(
                kind="inventoryConflict",
                label="Inventory conflict",
                record_id=str(conflict.id),
                subject=(
                    f"{older.customer.name if older else 'Older batch'} ↔ "
                    f"{newer.customer.name if newer else 'Newer batch'}"
                ),
                detail=f"{_plural(overlap, 'overlapping lead')}",
                since=conflict.created_at,
                href=f"/app/admin/fulfillment/conflicts/{conflict.id}",
            )
        )
    return decisions


def _report_decisions(db: Session) -> list[dict[str, object]]:
    """Open Lead Reports, each with its Eligibility Hold folded in.

    A hold is released only by resolving its report, so it is one decision,
    not two rows.
    """
    reports = list(
        db.scalars(
            select(LeadReport)
            .where(LeadReport.status == "open")
            .order_by(LeadReport.created_at.asc())
            .limit(DECISION_LIMIT)
        )
    )
    if not reports:
        return []
    held = set(
        db.scalars(
            select(EligibilityHold.report_id).where(
                EligibilityHold.report_id.in_({item.id for item in reports}),
                EligibilityHold.active.is_(True),
            )
        )
    )
    customers = {
        customer.id: customer.name
        for customer in db.scalars(
            select(Customer).where(
                Customer.id.in_({item.customer_id for item in reports})
            )
        )
    }
    return [
        _decision(
            kind="leadReport",
            label="Lead report",
            record_id=str(item.id),
            subject=customers.get(item.customer_id, "Customer"),
            detail=(
                _REPORT_REASONS.get(item.reason, item.reason)
                + (" · hold active" if item.id in held else "")
            ),
            since=item.created_at,
            href=f"/app/admin/fulfillment/reports/{item.id}",
        )
        for item in reports
    ]


def _job_decisions(db: Session) -> list[dict[str, object]]:
    """Failed jobs, except those behind a failed Batch: that Batch is the decision."""
    failed_batches = select(LeadRequest.id).where(
        LeadRequest.status == RequestStatus.failed.value
    )
    decisions = []
    for job in db.scalars(
        select(Job)
        .where(
            Job.status == JobStatus.failed.value,
            or_(Job.request_id.is_(None), Job.request_id.not_in(failed_batches)),
        )
        .order_by(Job.created_at.asc(), Job.id.asc())
        .limit(DECISION_LIMIT)
    ):
        batch = db.get(LeadRequest, job.request_id) if job.request_id else None
        name = _JOB_LABELS.get(job.kind, job.kind.replace("_", " ").capitalize())
        destination = _job_destination(job, db)
        href = batch_href(batch) if batch is not None else destination.href
        error = (job.last_error or "").strip()
        decisions.append(
            _decision(
                kind="failedJob",
                label="Failed job",
                record_id=str(job.id),
                subject=f"{name} #{batch.number}" if batch is not None else name,
                detail=(
                    f"Failed after {_plural(job.attempts, 'attempt')}"
                    + (f" · {error[:60]}" if error else "")
                ),
                since=job.created_at,
                href=href,
                batch=batch,
                facts={
                    "job": job.id,
                    "kind": job.kind,
                    "attempts": job.attempts,
                    "lastError": error,
                    "affected": {"label": destination.label, "href": href},
                },
            )
        )
    return decisions


def _anomaly_decisions(db: Session) -> list[dict[str, object]]:
    decisions = []
    for anomaly in db.scalars(
        select(ScrapeAnomaly)
        .where(ScrapeAnomaly.status == "pending")
        .order_by(ScrapeAnomaly.created_at.asc())
        .limit(DECISION_LIMIT)
    ):
        segments = _anomalous_segments(db, anomaly)
        first = segments[0] if segments else None
        detail = (
            _segment_label(str(first["key"]))
            + " · "
            + ", ".join(
                _ANOMALY_REASONS.get(reason, reason.replace("_", " "))
                for reason in first["reasons"]
            )
            if first is not None
            else "Held before publication"
        )
        if len(segments) > 1:
            detail += f" · {len(segments) - 1:,} more"
        decisions.append(
            _decision(
                kind="scrapeAnomaly",
                label="Scrape anomaly",
                record_id=str(anomaly.id),
                subject=f"Run {anomaly.scraper_run_id}",
                detail=detail,
                since=anomaly.created_at,
                href=f"/app/admin/acquisition/runs/{anomaly.scraper_run_id}",
                facts={
                    "runId": anomaly.scraper_run_id,
                    "segments": [
                        {
                            "key": segment["key"],
                            "label": _segment_label(str(segment["key"])),
                            "reasons": [
                                _ANOMALY_REASONS.get(reason, reason.replace("_", " "))
                                for reason in segment["reasons"]
                            ],
                        }
                        for segment in segments
                    ],
                },
            )
        )
    return decisions


def _mapping_decisions(db: Session) -> list[dict[str, object]]:
    return [
        _decision(
            kind="nicheMapping",
            label="Niche mapping",
            record_id=mapping.segment_key,
            subject=f"{mapping.keyword} · {mapping.state}",
            detail=f"Proposed {mapping.niche}" if mapping.niche else "No niche proposed",
            since=mapping.updated_at,
            href=f"/app/admin/setup/niche-mappings?segment={quote(mapping.segment_key, safe='')}",
            facts=niche_dict(mapping),
        )
        for mapping in db.scalars(
            select(SourceNicheMapping)
            .where(
                and_(
                    SourceNicheMapping.confirmed.is_(False),
                    SourceNicheMapping.denied_at.is_(None),
                )
            )
            .order_by(SourceNicheMapping.updated_at.asc())
            .limit(DECISION_LIMIT)
        )
    ]


def _exclusion_decisions(db: Session) -> list[dict[str, object]]:
    decisions = []
    for item in db.scalars(
        select(ExclusionList)
        .where(ExclusionList.status == "pending_confirmation")
        .order_by(ExclusionList.created_at.asc())
        .limit(DECISION_LIMIT)
    ):
        facts = _exclusion_dict(db, item)
        in_pool = item.pool_impact
        detail = _EXCLUSION_TYPES.get(item.exclusion_type, item.exclusion_type)
        detail += (
            f" · {int(in_pool):,} in pool"
            if isinstance(in_pool, int)
            else f" · {_plural(item.accepted_rows, 'phone')}"
        )
        decisions.append(
            _decision(
                kind="exclusionList",
                label="Exclusion list",
                record_id=str(item.id),
                subject=str(facts["customerName"]),
                detail=detail,
                since=item.created_at,
                href=f"/app/admin/inventory/exclusions?list={item.id}",
                facts={
                    **facts,
                    "typeLabel": _EXCLUSION_TYPES.get(
                        item.exclusion_type, item.exclusion_type
                    ),
                },
            )
        )
    return decisions


def _registration_decisions(db: Session) -> list[dict[str, object]]:
    decisions = []
    for item in db.scalars(
        select(CustomerRegistration)
        .where(CustomerRegistration.status == "awaiting_review")
        .order_by(CustomerRegistration.created_at.asc())
        .limit(DECISION_LIMIT)
    ):
        matches = archived_customer_matches(db, item.first_name, item.last_name)
        decisions.append(
            _decision(
                kind="registration",
                label="Registration",
                record_id=str(item.auth_user_id),
                subject=f"{item.first_name} {item.last_name}".strip() or item.email,
                detail=(
                    f"{item.email} · "
                    + _plural(len(matches), "archived match", "archived matches")
                ),
                since=item.created_at,
                href="/app/admin/customers#registrations",
                facts={
                    **registration_status(item),
                    "id": str(item.auth_user_id),
                    "matches": matches,
                },
            )
        )
    return decisions


_DECISION_READERS = (
    ("batches", _batch_decisions),
    ("inventoryConflicts", _conflict_decisions),
    ("leadReports", _report_decisions),
    ("failedJobs", _job_decisions),
    ("scrapeAnomalies", _anomaly_decisions),
    ("nicheMappings", _mapping_decisions),
    ("exclusionLists", _exclusion_decisions),
    ("registrations", _registration_decisions),
)


def decisions(db: Session) -> dict[str, object]:
    """Every waiting decision, oldest first."""
    items: list[dict[str, object]] = []
    unavailable: list[str] = []
    for key, reader in _DECISION_READERS:
        try:
            with db.begin_nested():
                items.extend(reader(db))
        except Exception:
            log.exception("Today decisions source %s failed", key)
            unavailable.append(key)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda item: (item["since"] or epoch, item["key"]))
    return {
        "items": items[:DECISION_LIMIT],
        "total": len(items),
        "unavailable": unavailable,
    }


def last_night(db: Session) -> dict[str, object] | None:
    """The latest committed Scrape Run and the Nightly Review that reported it."""
    publication = db.scalar(
        select(DatasetPublication).order_by(DatasetPublication.committed_at.desc()).limit(1)
    )
    review = db.scalar(
        select(NightlyReview).order_by(NightlyReview.created_at.desc()).limit(1)
    )
    if publication is None and review is None:
        return None
    new_leads = None
    if publication is not None:
        attempt = db.scalar(
            select(InventorySyncAttempt)
            .where(
                InventorySyncAttempt.dataset_publication_id == publication.id,
                InventorySyncAttempt.status == "complete",
            )
            .order_by(InventorySyncAttempt.attempt_number.desc())
            .limit(1)
        )
        imported = (attempt.result or {}).get("imported") if attempt else None
        new_leads = int(imported) if isinstance(imported, int) else None
    held_runs = select(ScrapeAnomaly.scraper_run_id).where(ScrapeAnomaly.status == "pending")
    held_segments = int(
        db.scalar(
            select(func.count(ScrapeSegmentResult.id)).where(
                ScrapeSegmentResult.scraper_run_id.in_(held_runs),
                ScrapeSegmentResult.anomalous.is_(True),
            )
        )
        or 0
    )
    return {
        "run": (
            {
                "id": publication.scraper_run_id,
                "committedAt": _utc(publication.committed_at),
                "syncStatus": publication.sync_status,
                "href": f"/app/admin/acquisition/runs/{publication.scraper_run_id}",
            }
            if publication is not None
            else None
        ),
        "newLeads": new_leads,
        "heldSegments": held_segments,
        "review": (
            {
                "id": str(review.id),
                "reviewDate": review.review_date,
                "status": review.status,
                "telegram": review.telegram_delivery_state,
                "telegramAt": _utc(review.telegram_delivery_started_at),
            }
            if review is not None
            else None
        ),
    }


def _batch_lists(
    db: Session, *, delivered_since: datetime, now: datetime
) -> dict[str, object]:
    base = select(LeadRequest).join(Customer, Customer.id == LeadRequest.agent_id)
    in_progress, in_progress_databases = batches_with_databases(
        db,
        base.where(LeadRequest.status.in_(IN_PROGRESS)).order_by(
            LeadRequest.created_at.asc()
        ),
    )
    delivered, delivered_databases = batches_with_databases(
        db,
        base.outerjoin(BatchArtifact, BatchArtifact.request_id == LeadRequest.id)
        .where(
            or_(
                and_(
                    LeadRequest.status == RequestStatus.delivered.value,
                    LeadRequest.delivered_at >= delivered_since,
                ),
                and_(
                    LeadRequest.status == RequestStatus.failed.value,
                    BatchArtifact.created_at >= delivered_since,
                ),
            )
        )
        .order_by(
            func.coalesce(LeadRequest.delivered_at, BatchArtifact.created_at).desc()
        ),
    )
    return {
        "inProgress": batch_rows(
            db, in_progress, databases=in_progress_databases, now=now
        ),
        "deliveredToday": [
            {
                **row,
                "fileAt": _utc(item.artifact.created_at) if item.artifact else None,
            }
            for item, row in zip(
                delivered,
                batch_rows(db, delivered, databases=delivered_databases, now=now),
            )
        ],
        "allBatches": int(db.scalar(select(func.count(LeadRequest.id))) or 0),
    }


def _guarded(db: Session, name: str, reader, fallback):
    try:
        with db.begin_nested():
            return reader()
    except Exception:
        log.exception("Today %s failed", name)
        return fallback


def today(
    db: Session,
    *,
    delivered_since: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """The Today read: one request for the whole page."""
    moment = _utc(now) or datetime.now(timezone.utc)
    since = _utc(delivered_since) or (moment - timedelta(hours=24))
    batches = _guarded(
        db,
        "batches",
        lambda: _batch_lists(db, delivered_since=since, now=moment),
        None,
    )
    return {
        "generatedAt": moment,
        "numbers": _guarded(db, "numbers", lambda: operator_numbers(db, now=moment), None),
        "lastNight": _guarded(db, "last night", lambda: last_night(db), None),
        # Live Scraper health (workers, queue) is read only through the
        # Scraper Privileged Session, so Today does not show it.
        "decisions": decisions(db),
        "batches": batches,
    }

