from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

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
    activation, and destructive or irreversible operations. Reads,
    transport bookkeeping, and idempotent no-ops do not belong here.

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
