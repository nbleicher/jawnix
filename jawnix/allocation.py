from __future__ import annotations

import csv
import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, exists, func, nullsfirst, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .jobs import enqueue_job
from .models import (
    Agency,
    Agent,
    BatchArtifact,
    DistributionEvent,
    Lead,
    LeadRequest,
    ListingObservation,
    RequestStatus,
)
from .states import truncate_utf8


@dataclass(frozen=True)
class AllocationResult:
    status: str
    allocated: int
    available: int
    artifact_id: int | None = None


def _same_recipient_clause(request: LeadRequest):
    if request.agent.agency_id is None:
        return DistributionEvent.agent_id == request.agent_id
    same_agency_agents = select(Agent.id).where(Agent.agency_id == request.agent.agency_id)
    return or_(
        DistributionEvent.agent_id == request.agent_id,
        DistributionEvent.agency_id == request.agent.agency_id,
        and_(
            DistributionEvent.agency_id.is_(None),
            DistributionEvent.agent_id.in_(same_agency_agents),
        ),
    )


def eligible_query(request: LeadRequest, settings: Settings):
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.global_cooldown_days)
    previously_sent = exists(
        select(DistributionEvent.id).where(
            DistributionEvent.lead_id == Lead.id,
            _same_recipient_clause(request),
        )
    )
    return (
        select(Lead)
        .where(
            Lead.state.in_(request.states_snapshot),
            or_(Lead.last_distributed_at.is_(None), Lead.last_distributed_at <= cutoff),
            ~previously_sent,
        )
        .order_by(nullsfirst(Lead.last_distributed_at), Lead.id)
    )


def inventory_count(session: Session, request: LeadRequest, settings: Settings) -> int:
    eligible_ids = eligible_query(request, settings).with_only_columns(Lead.id).order_by(None).subquery()
    return int(session.scalar(select(func.count()).select_from(eligible_ids)) or 0)


def _artifact_path(settings: Settings, request: LeadRequest) -> tuple[Path, str]:
    date_text = datetime.now(timezone.utc).date().isoformat()
    filename = f"{request.agent.slug}_batch_{request.id}_{date_text}.csv"
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
    if observation is None:
        return (
            {"kind": "legacy", "source": lead.source_flow},
            "legacy",
            "",
            "",
        )
    segment_key = (
        f"{observation.niche.lower()}|{observation.state}|"
        f"{observation.source.lower()}"
    )
    return (
        {
            "kind": "current_listing",
            "observationId": observation.id,
            "datasetChecksum": observation.dataset_checksum,
            "source": observation.source,
            "niche": observation.niche,
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
        )
        session.add(artifact)
    else:
        artifact.path = str(final_path)
        artifact.filename = filename
        artifact.row_count = len(rows)
        artifact.byte_count = final_path.stat().st_size
        artifact.sha256 = digest
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
                agent_id=request.agent_id,
                customer_name=request.agent.name,
                agency_id=request.agent.agency_id,
                agency_name=request.agent.agency.name if request.agent.agency else "",
                request_id=request.id,
                phone=lead.phone,
                title=lead.title,
                state=lead.state,
                listing_provenance=provenance,
                source_kind=source_kind,
                source_segment_key=segment_key,
                source_niche=niche,
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
            not item.agent.active
            or item.agent.deleted_at is not None
            or (
                item.agent.agency is not None
                and (
                    not item.agent.agency.active
                    or item.agent.agency.deleted_at is not None
                )
            )
        ):
            continue
        key = (
            ("agency", item.agent.agency_id)
            if item.agent.agency_id is not None
            else ("customer", item.agent_id)
        )
        grouped.setdefault(key, []).append(item)

    def group_order(entry: tuple[tuple[str, int], list[LeadRequest]]):
        key, items = entry
        customer = items[0].agent
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
                item.agent.last_fulfilled_at is not None,
                item.agent.last_fulfilled_at
                or datetime.min.replace(tzinfo=timezone.utc),
                item.agent_id,
                item.approved_at
                or item.created_at,
                item.id,
            )
        )
        item = agency_requests[0]
        if item.status == RequestStatus.waiting_inventory.value:
            item.status = RequestStatus.approved.value
            item.status_message = "New committed inventory is being checked."
        result = allocate_request(session, item.id, settings)
        if result.status == RequestStatus.generated.value:
            fulfilled += 1
            fulfilled_at = item.processed_at or datetime.now(timezone.utc)
            item.agent.last_fulfilled_at = fulfilled_at
            if item.agent.agency is not None:
                item.agent.agency.last_fulfilled_at = fulfilled_at
        elif result.status == RequestStatus.waiting_inventory.value:
            waiting += 1
    session.flush()
    return {
        "agenciesVisited": visited,
        "requestsFulfilled": fulfilled,
        "requestsWaiting": waiting,
    }
