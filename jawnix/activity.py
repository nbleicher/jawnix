from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import AuditEntry


_SECRET_KEY_PARTS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "handoff",
    "password",
    "refresh_token",
    "secret",
    "session",
    "signature",
    "token",
}


class UnsafeActivityDetailsError(ValueError):
    """Raised before secret-bearing details can reach an Audit Entry."""


ACTIVITY_PAGE_SIZE = 25
MAX_ACTIVITY_PAGE_SIZE = 100


_ENTITY_PATHS = {
    "agency": "/app/admin/agencies/{id}",
    "batch_request": "/app/admin/fulfillment/requests/{id}",
    "customer": "/app/admin/customers/{id}",
    "inventory_conflict": "/app/admin/fulfillment/conflicts/{id}",
    "lead_report": "/app/admin/fulfillment/reports/{id}",
    "scrape_run": "/app/admin/acquisition/runs/{id}",
    "scraper_configuration": "/app/admin/acquisition/configurations/{id}",
}

_ENTITY_DESTINATIONS = {
    "administrator_mfa": "/app/admin/security",
    "lead": "/app/admin/fulfillment",
    "nightly_review": "/app/admin/acquisition",
    "scrape_anomaly": "/app/admin/acquisition",
    "scraper_pipeline": "/app/admin/acquisition/scraper/workspace",
    "scraper_privileged_session": "/app/admin/acquisition/scraper",
    "scraper_keywords": (
        "/app/admin/acquisition/scraper/workspace/keywords"
    ),
    "scraper_keyword_generation": (
        "/app/admin/acquisition/scraper/workspace/keywords"
    ),
    "scraper_keyword_rollover": (
        "/app/admin/acquisition/scraper/workspace/keywords"
    ),
    "scraper_export": (
        "/app/admin/acquisition/scraper/workspace/database"
    ),
    "scraper_runtime_configuration": (
        "/app/admin/acquisition/scraper/workspace/runtime"
    ),
    "source_recommendation": "/app/admin/acquisition",
    "exclusion_list": "/app/admin/acquisition",
    "source_segment": "/app/admin/acquisition",
    "user_account": "/app/admin/customers",
}


def _normalized_key(key: object) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _assert_safe_detail_keys(value: object, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = _normalized_key(raw_key)
            if any(part in key for part in _SECRET_KEY_PARTS):
                location = ".".join((*path, str(raw_key)))
                raise UnsafeActivityDetailsError(
                    f"Activity details contain a secret-bearing key: {location}"
                )
            _assert_safe_detail_keys(nested, (*path, str(raw_key)))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_safe_detail_keys(nested, (*path, str(index)))


def _contains_secret(value: object, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, Mapping):
        return any(
            _contains_secret(key, secret)
            or _contains_secret(nested, secret)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(nested, secret) for nested in value)
    return False


def _activity_entity_href(target_type: str, target_id: str) -> str:
    if path := _ENTITY_PATHS.get(target_type):
        return path.format(id=quote(target_id, safe=""))
    if destination := _ENTITY_DESTINATIONS.get(target_type):
        return destination
    return (
        "/app/admin/activity?"
        f"entityType={quote(target_type, safe='')}&"
        f"entityId={quote(target_id, safe='')}"
    )


def _activity_entry(entry: AuditEntry) -> dict[str, object]:
    return {
        "id": str(entry.id),
        "action": entry.action,
        "entityType": entry.target_type,
        "entityId": entry.target_id,
        "entityHref": _activity_entity_href(
            entry.target_type,
            entry.target_id,
        ),
        "actor": entry.actor_user_id,
        "reason": entry.reason,
        "details": entry.details or {},
        "recordedAt": entry.created_at,
    }


def query_activity(
    session: Session,
    *,
    actor: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = ACTIVITY_PAGE_SIZE,
    query: str | None = None,
) -> dict[str, object]:
    """Read global Activity and entity timelines through one query seam."""

    normalized_page = max(1, page)
    normalized_page_size = min(
        MAX_ACTIVITY_PAGE_SIZE,
        max(1, page_size),
    )
    filters = []
    if query and query.strip():
        term = f"%{query.strip()}%"
        filters.append(
            or_(
                AuditEntry.action.ilike(term),
                AuditEntry.target_type.ilike(term),
                AuditEntry.target_id.ilike(term),
                AuditEntry.actor_user_id.ilike(term),
                AuditEntry.reason.ilike(term),
            )
        )
    if actor and actor.strip():
        filters.append(AuditEntry.actor_user_id == actor.strip())
    if action and action.strip():
        filters.append(AuditEntry.action == action.strip())
    if entity_type and entity_type.strip():
        filters.append(AuditEntry.target_type == entity_type.strip())
    if entity_id and entity_id.strip():
        filters.append(AuditEntry.target_id == entity_id.strip())
    if date_from is not None:
        filters.append(
            AuditEntry.created_at
            >= datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        )
    if date_to is not None:
        filters.append(
            AuditEntry.created_at
            < datetime.combine(
                date_to + timedelta(days=1),
                time.min,
                tzinfo=timezone.utc,
            )
        )

    total = session.scalar(
        select(func.count()).select_from(AuditEntry).where(*filters)
    ) or 0
    pages = max(1, (total + normalized_page_size - 1) // normalized_page_size)
    bounded_page = min(normalized_page, pages)
    entries = session.scalars(
        select(AuditEntry)
        .where(*filters)
        .order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc())
        .offset((bounded_page - 1) * normalized_page_size)
        .limit(normalized_page_size)
    )
    return {
        "entries": [_activity_entry(entry) for entry in entries],
        "page": bounded_page,
        "pageSize": normalized_page_size,
        "total": total,
        "pages": pages,
    }


def record_activity(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: object,
    actor_id: object,
    reason: str,
    details: Mapping[str, Any] | None = None,
    known_secrets: Iterable[str] = (),
) -> AuditEntry:
    """Record one consequential durable change through the shared write seam.

    Consequential actions are durable state transitions, eligibility or
    suppression changes, identity or membership changes, configuration
    activation, and destructive or irreversible operations. Ordinary reads,
    transport bookkeeping, and idempotent no-ops do not belong here. Retrieval
    of a retained Batch Artifact is the deliberate exception: the file is
    sensitive Customer data and ADR 0015 requires immutable download evidence.

    The caller supplies a deliberately small, safe before/after summary. A
    reason is always required, system work uses a stable ``system:`` actor,
    and secret-bearing keys or known secret material are rejected before an
    Audit Entry is added. The helper deliberately does not commit: successful
    actions and their evidence must commit atomically. For a refused action,
    callers must first roll back the failed transaction, then call this helper
    and commit the evidence in a fresh transaction.
    """
    normalized_action = action.strip()
    normalized_target_type = target_type.strip()
    normalized_actor = str(actor_id).strip()
    normalized_reason = reason.strip()
    safe_details = dict(details or {})
    if not normalized_action:
        raise ValueError("Activity action is required.")
    if not normalized_target_type:
        raise ValueError("Activity target type is required.")
    if not normalized_actor:
        raise ValueError("Activity actor is required.")
    if not normalized_reason:
        raise ValueError("Activity reason is required.")
    _assert_safe_detail_keys(safe_details)
    try:
        json.dumps(
            safe_details,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Activity details must be JSON-safe.") from exc
    for secret in known_secrets:
        if secret and (
            _contains_secret(
                {
                    "action": normalized_action,
                    "targetType": normalized_target_type,
                    "targetId": str(target_id),
                    "actorId": normalized_actor,
                    "reason": normalized_reason,
                    "details": safe_details,
                },
                secret,
            )
        ):
            raise UnsafeActivityDetailsError(
                "Activity details contain known secret material."
            )
    entry = AuditEntry(
        action=normalized_action,
        target_type=normalized_target_type,
        target_id=str(target_id),
        actor_user_id=normalized_actor,
        reason=normalized_reason,
        details=safe_details,
    )
    session.add(entry)
    return entry
