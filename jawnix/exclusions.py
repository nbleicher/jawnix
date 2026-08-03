"""Exclusion List ingestion and standing allocation protection."""

from __future__ import annotations

import csv
import uuid
from pathlib import Path

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from .activity import record_activity
from .jobs import enqueue_job
from .models import (
    EligibilityHold,
    ExclusionList,
    ExclusionPhone,
    Lead,
    NightlyReview,
    utcnow,
)
from .states import normalize_phone


EXCLUSION_TYPES = frozenset({"landline", "dnc", "tcpa_litigator"})
MIN_ROWS = 1_000
MAX_ROWS = 50_000


class ExclusionDecisionError(Exception):
    pass


def exclusion_list_status(item: ExclusionList, session: Session | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(item.id),
        "type": item.exclusion_type,
        "filename": item.filename,
        "status": item.status,
        "totalRows": item.total_rows,
        "acceptedRows": item.accepted_rows,
        "invalidRows": item.invalid_rows,
        "duplicateRows": item.duplicate_rows,
        "poolImpact": item.pool_impact,
        "global": item.global_effective,
        "error": item.error,
        "createdAt": item.created_at,
        "ingestedAt": item.ingested_at,
        "decidedAt": item.decided_at,
    }
    if session is not None:
        from .pool_analytics import customer_availability_preview

        payload["customerAvailability"] = customer_availability_preview(
            session, item.customer_id
        )
    return payload


def _phone_column(rows: list[list[str]]) -> tuple[int, list[list[str]]]:
    if not rows:
        return 0, []
    header = [value.strip().casefold().replace(" ", "_") for value in rows[0]]
    for name in ("phone", "phone_number", "telephone"):
        if name in header:
            return header.index(name), rows[1:]
    return 0, rows


def _currently_available_pool_impact(
    session: Session,
    exclusion_list_id: uuid.UUID,
) -> int:
    actively_held = exists(
        select(EligibilityHold.id).where(
            EligibilityHold.lead_id == Lead.id,
            EligibilityHold.active.is_(True),
        )
    )
    already_global = exists(
        select(ExclusionPhone.phone)
        .join(
            ExclusionList,
            ExclusionList.id == ExclusionPhone.exclusion_list_id,
        )
        .where(
            ExclusionPhone.phone == Lead.phone,
            ExclusionList.global_effective.is_(True),
            ExclusionList.id != exclusion_list_id,
        )
    )
    uploaded_phone = exists(
        select(ExclusionPhone.phone).where(
            ExclusionPhone.exclusion_list_id == exclusion_list_id,
            ExclusionPhone.phone == Lead.phone,
        )
    )
    return int(
        session.scalar(
            select(func.count(Lead.id)).where(
                Lead.suppressed.is_(False),
                ~actively_held,
                ~already_global,
                uploaded_phone,
            )
        )
        or 0
    )


def refresh_pool_impact(
    session: Session,
    item: ExclusionList,
) -> int:
    item.pool_impact = _currently_available_pool_impact(session, item.id)
    return item.pool_impact


def ingest_exclusion_list(
    session: Session,
    exclusion_list_id: uuid.UUID,
) -> ExclusionList:
    item = session.scalar(
        select(ExclusionList)
        .where(ExclusionList.id == exclusion_list_id)
        .with_for_update()
    )
    if item is None:
        raise LookupError("Exclusion List was not found.")
    if item.status != "queued":
        return item

    item.status = "ingesting"
    try:
        with Path(item.storage_path).open(
            "r", encoding="utf-8-sig", newline=""
        ) as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeError, csv.Error):
        item.status = "failed"
        item.error = "The CSV could not be read."
        return item

    column, data_rows = _phone_column(rows)
    item.total_rows = len(data_rows)
    if not MIN_ROWS <= item.total_rows <= MAX_ROWS:
        item.status = "failed"
        item.error = (
            f"Exclusion Lists must contain {MIN_ROWS:,}–{MAX_ROWS:,} data rows."
        )
        return item

    phones: set[str] = set()
    for row in data_rows:
        raw_phone = row[column] if column < len(row) else ""
        phone = normalize_phone(raw_phone)
        if phone is None:
            item.invalid_rows += 1
        elif phone in phones:
            item.duplicate_rows += 1
        else:
            phones.add(phone)
    item.accepted_rows = len(phones)
    session.add_all(
        ExclusionPhone(exclusion_list_id=item.id, phone=phone)
        for phone in sorted(phones)
    )
    session.flush()
    refresh_pool_impact(session, item)
    item.ingested_at = utcnow()
    if item.customer_id is None:
        item.status = "confirmed"
        item.global_effective = True
        item.decided_at = item.ingested_at
        item.decision_actor = item.uploaded_by
        if not item.decision_reason:
            item.decision_reason = "Administrator bulk upload"
    else:
        item.status = "pending_confirmation"
    record_activity(
        session,
        action="exclusion_list_ingested",
        target_type="exclusion_list",
        target_id=item.id,
        actor_id=(
            item.uploaded_by
            if item.customer_id is None
            else "system:exclusion-ingestion"
        ),
        reason=(
            item.decision_reason
            if item.customer_id is None
            else "Exclusion List background ingestion completed"
        ),
        details={
            "customerId": item.customer_id,
            "type": item.exclusion_type,
            "acceptedRows": item.accepted_rows,
            "invalidRows": item.invalid_rows,
            "duplicateRows": item.duplicate_rows,
            "poolImpact": item.pool_impact,
            "global": item.global_effective,
        },
    )
    return item


def decide_exclusion_list(
    session: Session,
    exclusion_list_id: uuid.UUID,
    action: str,
    *,
    actor_id: object,
    reason: str,
) -> ExclusionList:
    if action not in {"confirm", "deny"}:
        raise ExclusionDecisionError("Unknown Exclusion List decision.")
    item = session.scalar(
        select(ExclusionList)
        .where(ExclusionList.id == exclusion_list_id)
        .with_for_update()
    )
    if item is None:
        raise LookupError("Exclusion List was not found.")
    if item.customer_id is None:
        raise ExclusionDecisionError(
            "Administrator uploads do not require confirmation."
        )
    if item.status != "pending_confirmation":
        raise ExclusionDecisionError(
            "Only a pending Exclusion List can be decided."
        )
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ExclusionDecisionError("A decision reason is required.")
    refresh_pool_impact(session, item)
    item.status = "confirmed" if action == "confirm" else "denied"
    item.global_effective = action == "confirm"
    item.decision_actor = str(actor_id)
    item.decision_reason = normalized_reason
    item.decided_at = utcnow()
    record_activity(
        session,
        action=f"exclusion_list_{item.status}",
        target_type="exclusion_list",
        target_id=item.id,
        actor_id=actor_id,
        reason=normalized_reason,
        details={
            "customerId": item.customer_id,
            "type": item.exclusion_type,
            "poolImpact": item.pool_impact,
            "global": item.global_effective,
        },
    )
    if item.nightly_review_id is not None:
        review = session.get(NightlyReview, item.nightly_review_id)
        if review is not None:
            rows = [
                {
                    **row,
                    "status": item.status,
                    "poolImpact": item.pool_impact,
                }
                if str(row.get("id")) == str(item.id)
                else row
                for row in (review.summary.get("exclusionLists") or [])
            ]
            review.summary = {**review.summary, "exclusionLists": rows}
            if review.telegram_message_id:
                enqueue_job(
                    session,
                    "update_nightly_review_notification",
                    payload={"review_id": str(review.id)},
                )
    return item
