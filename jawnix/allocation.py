from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, exists, func, nullsfirst, or_, select
from sqlalchemy.orm import Session

from .activity import record_activity
from .config import Settings
from .jobs import enqueue_job
from .milestone_emails import enqueue_milestone_email
from .models import (
    Agency,
    Customer,
    BatchArtifact,
    DatasetPublication,
    DistributionEvent,
    EligibilityHold,
    InventoryConflict,
    Lead,
    LeadCorrectionEvent,
    LeadRequest,
    ListingObservation,
    RequestStatus,
    ScraperConfiguration,
    ScraperRun,
)
from .states import truncate_utf8


@dataclass(frozen=True)
class AllocationResult:
    status: str
    allocated: int
    available: int
    artifact_id: int | None = None


def _same_recipient_clause(request: LeadRequest):
    permanent_customers = select(Customer.id).where(
        Customer.permanent_history_key
        == request.customer.permanent_history_key
    )
    permanent_agencies = select(Agency.id).where(
        Agency.permanent_history_key
        == request.customer.permanent_history_key
    )
    permanent_history = or_(
        DistributionEvent.agent_id.in_(permanent_customers),
        DistributionEvent.agency_id.in_(permanent_agencies),
    )
    if request.customer.agency_id is None:
        return or_(
            DistributionEvent.agent_id == request.customer_id,
            permanent_history,
        )
    same_agency_customers = select(Customer.id).where(
        Customer.agency_id == request.customer.agency_id
    )
    return or_(
        DistributionEvent.agent_id == request.customer_id,
        DistributionEvent.agency_id == request.customer.agency_id,
        and_(
            DistributionEvent.agency_id.is_(None),
            DistributionEvent.agent_id.in_(same_agency_customers),
        ),
        permanent_history,
    )


def eligible_query(request: LeadRequest, settings: Settings):
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.global_cooldown_days)
    previously_sent = exists(
        select(DistributionEvent.id).where(
            DistributionEvent.lead_id == Lead.id,
            _same_recipient_clause(request),
        )
    )
    actively_held = exists(
        select(EligibilityHold.id).where(
            EligibilityHold.lead_id == Lead.id,
            EligibilityHold.active.is_(True),
        )
    )
    return (
        select(Lead)
        .where(
            Lead.state.in_(request.states_snapshot),
            Lead.suppressed.is_(False),
            or_(Lead.last_distributed_at.is_(None), Lead.last_distributed_at <= cutoff),
            ~previously_sent,
            ~actively_held,
        )
        .order_by(nullsfirst(Lead.last_distributed_at), Lead.id)
    )


def inventory_count(session: Session, request: LeadRequest, settings: Settings) -> int:
    eligible_ids = eligible_query(request, settings).with_only_columns(Lead.id).order_by(None).subquery()
    return int(session.scalar(select(func.count()).select_from(eligible_ids)) or 0)


def _artifact_path(settings: Settings, request: LeadRequest) -> tuple[Path, str]:
    date_text = datetime.now(timezone.utc).date().isoformat()
    filename = (
        f"{request.customer.slug}_batch_{request.id}_{date_text}.csv"
    )
    directory = Path(settings.batch_dir) / date_text
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename, filename


def _listing_snapshot(
    session: Session,
    lead: Lead,
) -> tuple[dict, str, str, str]:
    observation = (
        session.get(ListingObservation, lead.current_listing_observation_id)
        if lead.current_listing_observation_id is not None
        else None
    )
    publication = (
        session.scalar(
            select(DatasetPublication).where(
                DatasetPublication.checksum
                == observation.dataset_checksum
            )
        )
        if observation is not None
        else None
    )
    scrape_run = (
        session.get(ScraperRun, publication.scraper_run_id)
        if publication is not None
        else None
    )
    configuration = (
        session.get(
            ScraperConfiguration,
            publication.configuration_id,
        )
        if publication is not None
        else None
    )
    acquisition_provenance = {
        "datasetVersion": (
            publication.version if publication is not None else None
        ),
        "scrapeRunId": scrape_run.id if scrape_run is not None else None,
        "configurationId": (
            str(configuration.id) if configuration is not None else None
        ),
        "configurationVersion": (
            configuration.version if configuration is not None else None
        ),
    }
    correction = (
        session.get(LeadCorrectionEvent, lead.active_correction_id)
        if lead.active_correction_id is not None
        else None
    )
    if correction is not None:
        segment_key = (
            observation.source
            if observation is not None
            else ""
        )
        provenance = {
            "kind": "lead_correction",
            "correctionId": str(correction.id),
        }
        if observation is not None:
            provenance["underlyingObservationId"] = observation.id
            provenance.update(acquisition_provenance)
        return (
            provenance,
            "google_maps" if observation is not None else "legacy",
            segment_key,
            observation.niche if observation is not None else "",
        )
    if observation is None:
        return (
            {"kind": "legacy", "source": lead.source_flow},
            "legacy",
            "",
            "",
        )
    segment_key = observation.source
    return (
        {
            "kind": "current_listing",
            "observationId": observation.id,
            "datasetChecksum": observation.dataset_checksum,
            "source": observation.source,
            "niche": observation.niche,
            **acquisition_provenance,
        },
        "google_maps",
        segment_key,
        observation.niche.lower(),
    )


def generate_artifact(
    session: Session,
    request: LeadRequest,
    rows: list[Lead] | list[DistributionEvent],
    settings: Settings,
) -> BatchArtifact:
    final_path, filename = _artifact_path(settings, request)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{request.id}-", suffix=".csv", dir=final_path.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["phone", "title"], lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({"phone": row.phone, "title": truncate_utf8(row.title)})
        os.replace(temp_path, final_path)
    finally:
        temp_path.unlink(missing_ok=True)
    digest = hashlib.sha256(final_path.read_bytes()).hexdigest()
    artifact = session.scalar(select(BatchArtifact).where(BatchArtifact.request_id == request.id))
    if artifact is None:
        artifact = BatchArtifact(
            request_id=request.id,
            path=str(final_path),
            filename=filename,
            row_count=len(rows),
            byte_count=final_path.stat().st_size,
            sha256=digest,
            expires_at=(
                datetime.now(timezone.utc)
                + timedelta(days=settings.batch_retention_days)
            ),
        )
        session.add(artifact)
    else:
        artifact.path = str(final_path)
        artifact.filename = filename
        artifact.row_count = len(rows)
        artifact.byte_count = final_path.stat().st_size
        artifact.sha256 = digest
        artifact.created_at = datetime.now(timezone.utc)
        artifact.expires_at = (
            artifact.created_at
            + timedelta(days=settings.batch_retention_days)
        )
    session.flush()
    return artifact


def allocate_request(session: Session, request_id: uuid.UUID, settings: Settings) -> AllocationResult:
    request = session.scalar(
        select(LeadRequest).where(LeadRequest.id == request_id).with_for_update()
    )
    if request is None:
        raise LookupError(f"Request {request_id} was not found.")

    existing_events = list(
        session.scalars(
            select(DistributionEvent).where(DistributionEvent.request_id == request.id).order_by(DistributionEvent.id)
        )
    )
    if existing_events:
        if len(existing_events) != request.lead_count:
            raise RuntimeError("Existing allocation has an unexpected row count.")
        if any(not event.phone for event in existing_events):
            leads_by_id = {
                lead.id: lead
                for lead in session.scalars(
                    select(Lead).where(
                        Lead.id.in_([event.lead_id for event in existing_events])
                    )
                )
            }
            for event in existing_events:
                lead = leads_by_id[event.lead_id]
                event.phone = lead.phone
                event.title = lead.title
                event.state = lead.state
                event.listing_provenance = {
                    "kind": "legacy",
                    "source": lead.source_flow,
                }
        artifact = generate_artifact(session, request, existing_events, settings)
        request.status = RequestStatus.generated.value
        request.status_message = "Batch generated; email delivery is queued."
        enqueue_job(session, "deliver_request", request.id)
        enqueue_job(session, "update_notification", request.id)
        return AllocationResult(
            request.status,
            len(existing_events),
            len(existing_events),
            artifact.id,
        )

    if request.status not in {RequestStatus.approved.value, RequestStatus.processing.value}:
        return AllocationResult(request.status, 0, request.available_count or 0)

    request.status = RequestStatus.processing.value
    request.status_message = "Selecting eligible inventory."
    session.flush()

    candidates = list(
        session.scalars(
            eligible_query(request, settings)
            .limit(request.lead_count)
            .with_for_update(skip_locked=True)
        )
    )
    if len(candidates) < request.lead_count:
        available = inventory_count(session, request, settings)
        request.status = RequestStatus.waiting_inventory.value
        request.available_count = available
        request.status_message = f"Inventory shortage: requested {request.lead_count:,}; available {available:,}. No rows were allocated."
        enqueue_milestone_email(session, request)
        enqueue_job(session, "update_notification", request.id)
        return AllocationResult(request.status, 0, available)

    distributed_at = datetime.now(timezone.utc)
    for lead in candidates:
        provenance, source_kind, segment_key, niche = _listing_snapshot(
            session,
            lead,
        )
        lead.last_distributed_at = distributed_at
        session.add(
            DistributionEvent(
                lead_id=lead.id,
                agent_id=request.customer_id,
                customer_name=request.customer.name,
                agency_id=request.customer.agency_id,
                agency_name=(
                    request.customer.agency.name
                    if request.customer.agency
                    else ""
                ),
                request_id=request.id,
                phone=lead.phone,
                title=lead.title,
                state=lead.state,
                listing_provenance=provenance,
                source_kind=source_kind,
                source_segment_key=segment_key,
                source_niche=niche,
                distribution_period=distributed_at.strftime("%Y-%m"),
                delivered_at=distributed_at,
                source="request",
            )
        )
    artifact = generate_artifact(session, request, candidates, settings)
    request.status = RequestStatus.generated.value
    request.available_count = len(candidates)
    request.processed_at = distributed_at
    request.status_message = "Batch generated; email delivery is queued."
    enqueue_job(session, "deliver_request", request.id)
    enqueue_job(session, "update_notification", request.id)
    session.flush()
    return AllocationResult(request.status, len(candidates), len(candidates), artifact.id)


def _inventory_conflict_blocks(
    session: Session,
    request: LeadRequest,
    settings: Settings,
) -> bool:
    newer_ids = list(
        session.scalars(
            eligible_query(request, settings)
            .with_only_columns(Lead.id)
            .order_by(None)
        )
    )
    if len(newer_ids) < request.lead_count:
        return False
    older_requests = list(
        session.scalars(
            select(LeadRequest)
            .where(
                LeadRequest.status
                == RequestStatus.waiting_inventory.value,
                LeadRequest.id != request.id,
                LeadRequest.created_at < request.created_at,
            )
            .order_by(LeadRequest.created_at, LeadRequest.id)
            .with_for_update(skip_locked=True)
        )
    )
    newer_id_set = set(newer_ids)
    for older in older_requests:
        if not set(older.states_snapshot).intersection(
            request.states_snapshot
        ):
            continue
        older_ids = list(
            session.scalars(
                eligible_query(older, settings)
                .with_only_columns(Lead.id)
                .order_by(None)
            )
        )
        shared_ids = sorted(newer_id_set.intersection(older_ids))
        if not shared_ids:
            continue
        snapshot = {
            "olderRequestId": str(older.id),
            "newerRequestId": str(request.id),
            "olderLeadCount": older.lead_count,
            "newerLeadCount": request.lead_count,
            "olderStates": sorted(older.states_snapshot),
            "newerStates": sorted(request.states_snapshot),
            "olderEligibleCount": len(older_ids),
            "newerEligibleCount": len(newer_ids),
            "sharedLeadIds": shared_ids,
        }
        checksum = hashlib.sha256(
            json.dumps(
                snapshot,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        conflict = session.scalar(
            select(InventoryConflict)
            .where(
                InventoryConflict.older_request_id == older.id,
                InventoryConflict.newer_request_id == request.id,
                InventoryConflict.snapshot_checksum == checksum,
            )
            .with_for_update()
        )
        if conflict is not None and conflict.status == "confirmed":
            conflict.status = "consumed"
            conflict.consumed_at = datetime.now(timezone.utc)
            return False
        if conflict is None:
            conflict = InventoryConflict(
                older_request_id=older.id,
                newer_request_id=request.id,
                inventory_snapshot=snapshot,
                snapshot_checksum=checksum,
                status="pending",
            )
            session.add(conflict)
            session.flush()
            enqueue_job(
                session,
                "notify_inventory_conflict",
                payload={"conflict_id": str(conflict.id)},
            )
        request.status = RequestStatus.waiting_inventory.value
        request.available_count = len(newer_ids)
        request.status_message = (
            "Waiting for an Inventory Conflict decision before shared "
            "inventory can bypass an older request."
        )
        enqueue_milestone_email(session, request)
        enqueue_job(session, "update_notification", request.id)
        return True
    return False


def decide_inventory_conflict(
    session: Session,
    conflict_id: uuid.UUID,
    action: str,
    actor_id: str,
    reason: str,
) -> dict:
    if action not in {"confirm", "deny"}:
        raise ValueError("Inventory Conflict action must be confirm or deny.")
    conflict = session.scalar(
        select(InventoryConflict)
        .where(InventoryConflict.id == conflict_id)
        .with_for_update()
    )
    if conflict is None:
        raise LookupError("Inventory Conflict was not found.")
    if conflict.status != "pending":
        return {
            "conflictId": str(conflict.id),
            "status": conflict.status,
            "duplicate": True,
        }
    conflict.status = "confirmed" if action == "confirm" else "denied"
    conflict.decision_by = actor_id
    conflict.decision_reason = reason.strip()
    conflict.decided_at = datetime.now(timezone.utc)
    record_activity(
        session,
        action=f"inventory_conflict_{conflict.status}",
        target_type="inventory_conflict",
        target_id=conflict.id,
        actor_id=actor_id,
        reason=reason,
        details={
            "before": {"status": "pending"},
            "after": {"status": conflict.status},
            "olderRequestId": str(conflict.older_request_id),
            "newerRequestId": str(conflict.newer_request_id),
            "snapshotChecksum": conflict.snapshot_checksum,
        },
    )
    enqueue_job(
        session,
        "update_inventory_conflict_notification",
        payload={"conflict_id": str(conflict.id)},
    )
    if action == "confirm":
        enqueue_job(session, "fulfill_round_robin")
    session.flush()
    return {
        "conflictId": str(conflict.id),
        "status": conflict.status,
    }


def fulfill_round_robin(session: Session, settings: Settings) -> dict[str, int]:
    """Give each Agency (or standalone Customer) at most one fulfillment turn."""
    requests = list(
        session.scalars(
            select(LeadRequest)
            .where(
                LeadRequest.status.in_(
                    {
                        RequestStatus.approved.value,
                        RequestStatus.waiting_inventory.value,
                    }
                )
            )
            .order_by(LeadRequest.created_at, LeadRequest.id)
            .with_for_update(skip_locked=True)
        )
    )
    grouped: dict[tuple[str, int], list[LeadRequest]] = {}
    for item in requests:
        if (
            not item.customer.active
            or item.customer.deleted_at is not None
            or (
                item.customer.agency is not None
                and (
                    not item.customer.agency.active
                    or item.customer.agency.deleted_at is not None
                )
            )
        ):
            continue
        key = (
            ("agency", item.customer.agency_id)
            if item.customer.agency_id is not None
            else ("customer", item.customer_id)
        )
        grouped.setdefault(key, []).append(item)

    def group_order(entry: tuple[tuple[str, int], list[LeadRequest]]):
        key, items = entry
        customer = items[0].customer
        last_fulfilled = (
            customer.agency.last_fulfilled_at
            if customer.agency is not None
            else customer.last_fulfilled_at
        )
        return (
            last_fulfilled is not None,
            last_fulfilled or datetime.min.replace(tzinfo=timezone.utc),
            key[0],
            key[1],
        )

    fulfilled = waiting = visited = 0
    for _, agency_requests in sorted(grouped.items(), key=group_order):
        visited += 1
        agency_requests.sort(
            key=lambda item: (
                item.customer.last_fulfilled_at is not None,
                item.customer.last_fulfilled_at
                or datetime.min.replace(tzinfo=timezone.utc),
                item.customer_id,
                item.approved_at
                or item.created_at,
                item.id,
            )
        )
        item = agency_requests[0]
        if _inventory_conflict_blocks(session, item, settings):
            waiting += 1
            continue
        if item.status == RequestStatus.waiting_inventory.value:
            item.status = RequestStatus.approved.value
            item.status_message = "New committed inventory is being checked."
        result = allocate_request(session, item.id, settings)
        if result.status == RequestStatus.generated.value:
            fulfilled += 1
            fulfilled_at = item.processed_at or datetime.now(timezone.utc)
            item.customer.last_fulfilled_at = fulfilled_at
            if item.customer.agency is not None:
                item.customer.agency.last_fulfilled_at = fulfilled_at
        elif result.status == RequestStatus.waiting_inventory.value:
            waiting += 1
    session.flush()
    return {
        "agenciesVisited": visited,
        "requestsFulfilled": fulfilled,
        "requestsWaiting": waiting,
    }
