"""Niche policy persistence, eligibility clauses, and assignment ingestion."""

from __future__ import annotations

import csv
import uuid
from collections import defaultdict
from pathlib import Path

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from .models import (
    Lead,
    ListingObservation,
    NicheAssignment,
    NicheAssignmentUpload,
    NichePolicyRow,
    SourceNicheMapping,
    utcnow,
)
from .states import US_STATES, normalize_phone


class NichePolicyError(ValueError):
    pass


def normalize_policy_rows(rows: list[dict]) -> list[dict]:
    """Validate and flatten API rows into one case-folded Niche per record."""
    normalized: list[dict] = []
    seen_states: dict[str | None, str] = {}
    seen: set[tuple[str | None, str]] = set()
    for row in rows:
        state_value = row.get("state")
        state = str(state_value).strip().upper() if state_value else None
        if state is not None and state not in US_STATES:
            raise NichePolicyError(f"Unsupported state code: {state}.")
        mode = str(row.get("mode", "")).strip().casefold()
        if mode not in {"exclude", "only"}:
            raise NichePolicyError("Niche Policy mode must be 'exclude' or 'only'.")
        prior = seen_states.get(state)
        if prior is not None and prior != mode:
            label = state or "all-states"
            raise NichePolicyError(f"{label} cannot mix exclude and only modes.")
        seen_states[state] = mode
        niches = row.get("niches") or []
        if not niches:
            raise NichePolicyError("Each Niche Policy row needs at least one Niche.")
        for raw_niche in niches:
            niche = str(raw_niche).strip()
            if not niche:
                raise NichePolicyError("Niches cannot be blank.")
            if len(niche) > 160:
                raise NichePolicyError("Niches must be at most 160 characters.")
            key = (state, niche.casefold())
            if key not in seen:
                seen.add(key)
                normalized.append({"state": state, "mode": mode, "niche": niche})
    return normalized


def load_policy(session: Session, customer_id: int) -> list[dict]:
    grouped: dict[tuple[str | None, str], list[str]] = defaultdict(list)
    rows = session.scalars(
        select(NichePolicyRow)
        .where(NichePolicyRow.customer_id == customer_id)
        .order_by(NichePolicyRow.state, NichePolicyRow.mode, NichePolicyRow.niche)
    )
    for row in rows:
        grouped[(row.state, row.mode)].append(row.niche)
    return [
        {"state": state, "mode": mode, "niches": niches}
        for (state, mode), niches in grouped.items()
    ]


def replace_policy(session: Session, customer_id: int, rows: list[dict]) -> list[dict]:
    normalized = normalize_policy_rows(rows)
    session.query(NichePolicyRow).filter(
        NichePolicyRow.customer_id == customer_id
    ).delete(synchronize_session=False)
    session.add_all(
        NichePolicyRow(customer_id=customer_id, **row) for row in normalized
    )
    session.flush()
    return load_policy(session, customer_id)


def effective_niche_expression():
    """Segment mapping wins over a manual phone assignment."""
    return case(
        (SourceNicheMapping.id.is_not(None), SourceNicheMapping.niche),
        else_=NicheAssignment.niche,
    )


def policy_clause(customer_id: int, niche_expression):
    """SQL condition for a Customer's effective state policy."""
    state_override = exists(
        select(NichePolicyRow.id).where(
            NichePolicyRow.customer_id == customer_id,
            NichePolicyRow.state == Lead.state,
        )
    )
    policy_scope = and_(
        NichePolicyRow.customer_id == customer_id,
        or_(
            NichePolicyRow.state == Lead.state,
            and_(NichePolicyRow.state.is_(None), ~state_override),
        ),
    )
    comparable_niche = and_(
        niche_expression.is_not(None),
        func.lower(NichePolicyRow.niche) == func.lower(niche_expression),
    )
    excluded = exists(
        select(NichePolicyRow.id).where(
            policy_scope, NichePolicyRow.mode == "exclude", comparable_niche
        )
    )
    has_only = exists(
        select(NichePolicyRow.id).where(
            policy_scope, NichePolicyRow.mode == "only"
        )
    )
    allowed_only = exists(
        select(NichePolicyRow.id).where(
            policy_scope, NichePolicyRow.mode == "only", comparable_niche
        )
    )
    return and_(~excluded, or_(~has_only, allowed_only))


def policy_clause_for_rows(rows: list[dict], niche_expression):
    """Same override-not-merge semantics against an in-memory draft policy."""

    normalized = normalize_policy_rows(rows)
    if not normalized:
        return True
    by_state: dict[str | None, list[dict]] = defaultdict(list)
    for row in normalized:
        by_state[row["state"]].append(row)
    specific_states = [state for state in by_state if state is not None]
    parts = []
    for state, state_rows in by_state.items():
        if state is None:
            state_match = (
                ~Lead.state.in_(specific_states) if specific_states else True
            )
        else:
            state_match = Lead.state == state
        mode = state_rows[0]["mode"]
        niches = [row["niche"].casefold() for row in state_rows]
        lowered = func.lower(niche_expression)
        if mode == "exclude":
            parts.append(
                and_(
                    state_match,
                    or_(
                        niche_expression.is_(None),
                        ~lowered.in_(niches),
                    ),
                )
            )
        else:
            parts.append(
                and_(state_match, lowered.in_(niches))
            )
    if specific_states and None not in by_state:
        # Unlisted states with no default: all niches allowed.
        parts.append(~Lead.state.in_(specific_states))
    return or_(*parts) if parts else True


def unmapped_inventory_query():
    """Inventory whose Current Listing has no source-derived Niche."""
    return (
        select(Lead)
        .outerjoin(
            ListingObservation,
            ListingObservation.id == Lead.current_listing_observation_id,
        )
        .outerjoin(
            SourceNicheMapping,
            SourceNicheMapping.segment_key == ListingObservation.source,
        )
        .where(SourceNicheMapping.id.is_(None))
        .order_by(Lead.state, Lead.title, Lead.phone)
    )


def upload_status(item: NicheAssignmentUpload) -> dict[str, object]:
    return {
        "id": str(item.id),
        "filename": item.filename,
        "status": item.status,
        "totalRows": item.total_rows,
        "acceptedRows": item.accepted_rows,
        "invalidRows": item.invalid_rows,
        "duplicateRows": item.duplicate_rows,
        "skippedMappedRows": item.skipped_mapped_rows,
        "error": item.error,
        "createdAt": item.created_at,
        "ingestedAt": item.ingested_at,
    }


def ingest_niche_assignments(
    session: Session, upload_id: uuid.UUID
) -> NicheAssignmentUpload:
    item = session.scalar(
        select(NicheAssignmentUpload)
        .where(NicheAssignmentUpload.id == upload_id)
        .with_for_update()
    )
    if item is None:
        raise LookupError("Niche Assignment upload was not found.")
    if item.status != "queued":
        return item
    item.status = "ingesting"
    try:
        with Path(item.storage_path).open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeError, csv.Error):
        item.status = "failed"
        item.error = "The CSV could not be read."
        return item
    if not rows:
        item.status = "failed"
        item.error = "The CSV must have phone,niche headers."
        return item
    headers = [value.strip().casefold().replace(" ", "_") for value in rows[0]]
    if "phone" not in headers or "niche" not in headers:
        item.status = "failed"
        item.error = "The CSV must have phone,niche headers."
        return item
    phone_column, niche_column = headers.index("phone"), headers.index("niche")
    assignments: dict[str, str] = {}
    item.total_rows = len(rows) - 1
    for row in rows[1:]:
        phone = normalize_phone(row[phone_column] if phone_column < len(row) else "")
        niche = (row[niche_column] if niche_column < len(row) else "").strip()
        if phone is None or not niche or len(niche) > 160:
            item.invalid_rows += 1
        elif phone.casefold() in assignments:
            item.duplicate_rows += 1
        else:
            assignments[phone] = niche
    mapped_phones = {
        phone
        for phone in session.scalars(
            select(Lead.phone)
            .join(
                ListingObservation,
                ListingObservation.id == Lead.current_listing_observation_id,
            )
            .join(
                SourceNicheMapping,
                SourceNicheMapping.segment_key == ListingObservation.source,
            )
            .where(Lead.phone.in_(assignments))
        )
    }
    item.skipped_mapped_rows = len(mapped_phones)
    accepted = {
        phone: niche for phone, niche in assignments.items() if phone not in mapped_phones
    }
    for phone, niche in accepted.items():
        existing = session.get(NicheAssignment, phone)
        if existing is None:
            session.add(NicheAssignment(phone=phone, niche=niche))
        else:
            existing.niche = niche
            existing.updated_at = utcnow()
    item.accepted_rows = len(accepted)
    item.status = "complete"
    item.ingested_at = utcnow()
    return item
