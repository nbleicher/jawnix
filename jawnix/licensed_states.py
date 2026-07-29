"""Safe Customer Licensed State review and application.

Licensed State changes are one decision with several durable consequences:
the Customer and profile mirrors change together, and every still-unallocated
Batch Request is either narrowed or canceled.  This module keeps preview and
apply on the same projection, then binds the preview to a signed checksum so a
concurrent change can never make the confirmed consequence stale.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Literal

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .activity import record_activity
from .config import Settings
from .customer_overview import build_customer_overview, customer_request_status
from .customer_requests import CANCELABLE_STATUSES, build_request_workspace
from .jobs import enqueue_job
from .models import CustomerProfile, LeadRequest, RequestStatus, utcnow
from .schemas import CustomerOverviewOut, CustomerRequestWorkspaceOut
from .states import US_STATE_OPTIONS, normalize_states


REVIEW_MAX_AGE_SECONDS = 15 * 60


class LicensedStateConflict(Exception):
    """The reviewed profile or request impact changed before confirmation."""


class LicensedStateReviewError(Exception):
    """The supplied review artifact is absent, invalid, or expired."""


class LicensedStateOption(BaseModel):
    code: str
    name: str


class LicensedStateWorkspace(BaseModel):
    states: list[str]
    options: list[LicensedStateOption]
    version: str


class LicensedStateSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    states: list[str]
    expected_version: str = Field(min_length=1, max_length=80)

    @field_validator("states")
    @classmethod
    def valid_states(cls, value: list[str]) -> list[str]:
        return normalize_states(value)


class LicensedStateImpact(BaseModel):
    request_id: uuid.UUID
    lead_count: int
    status: str
    current_states: list[str]
    resulting_states: list[str]
    action: Literal["narrowed", "canceled"]


class LicensedStateReview(BaseModel):
    current_states: list[str]
    proposed_states: list[str]
    added_states: list[str]
    removed_states: list[str]
    additions_apply_to_future_requests_only: bool = True
    impacts: list[LicensedStateImpact]
    review_token: str


class LicensedStateConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_token: str = Field(min_length=1, max_length=4096)


class LicensedStateApplyResult(BaseModel):
    account: LicensedStateWorkspace
    overview: CustomerOverviewOut
    requests: CustomerRequestWorkspaceOut


class _ReviewClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    version: str
    proposed_states: list[str]
    impact_checksum: str

    @field_validator("proposed_states")
    @classmethod
    def valid_states(cls, value: list[str]) -> list[str]:
        return normalize_states(value)


def _version(profile: CustomerProfile) -> str:
    return profile.updated_at.isoformat()


def _options() -> list[LicensedStateOption]:
    return [
        LicensedStateOption(code=code, name=name)
        for code, name in US_STATE_OPTIONS
    ]


def workspace(profile: CustomerProfile) -> LicensedStateWorkspace:
    return LicensedStateWorkspace(
        states=normalize_states(profile.licensed_states),
        options=_options(),
        version=_version(profile),
    )


def _affected_requests(
    db: Session,
    *,
    user_id: uuid.UUID,
    proposed_states: list[str],
    lock: bool,
) -> tuple[list[LeadRequest], list[LicensedStateImpact]]:
    statement = (
        select(LeadRequest)
        .where(
            LeadRequest.user_id == user_id,
            LeadRequest.status.in_(CANCELABLE_STATUSES),
        )
        .order_by(LeadRequest.created_at, LeadRequest.id)
    )
    if lock:
        statement = statement.with_for_update()
    requests = list(db.scalars(statement))
    impacts: list[LicensedStateImpact] = []
    for item in requests:
        current_states = normalize_states(item.states_snapshot)
        resulting_states = [
            state for state in current_states if state in proposed_states
        ]
        if resulting_states == current_states:
            continue
        impacts.append(
            LicensedStateImpact(
                request_id=item.id,
                lead_count=item.lead_count,
                status=customer_request_status(item.status).label,
                current_states=current_states,
                resulting_states=resulting_states,
                action="narrowed" if resulting_states else "canceled",
            )
        )
    return requests, impacts


def _impact_checksum(impacts: list[LicensedStateImpact]) -> str:
    payload = [
        impact.model_dump(mode="json")
        for impact in impacts
    ]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="licensed-state-impact-review",
    )


def preview(
    db: Session,
    *,
    user_id: uuid.UUID,
    profile: CustomerProfile,
    selection: LicensedStateSelection,
    settings: Settings,
) -> LicensedStateReview:
    current_states = normalize_states(profile.licensed_states)
    if selection.expected_version != _version(profile):
        raise LicensedStateConflict(
            "Licensed States changed in another session. "
            "Review the latest impact before saving."
        )
    proposed_states = selection.states
    if proposed_states == current_states:
        raise LicensedStateReviewError(
            "Choose a different set of Licensed States before reviewing."
        )
    _, impacts = _affected_requests(
        db,
        user_id=user_id,
        proposed_states=proposed_states,
        lock=False,
    )
    claims = _ReviewClaims(
        user_id=user_id,
        version=_version(profile),
        proposed_states=proposed_states,
        impact_checksum=_impact_checksum(impacts),
    )
    token = _serializer(settings).dumps(claims.model_dump(mode="json"))
    return LicensedStateReview(
        current_states=current_states,
        proposed_states=proposed_states,
        added_states=sorted(set(proposed_states) - set(current_states)),
        removed_states=sorted(set(current_states) - set(proposed_states)),
        impacts=impacts,
        review_token=token,
    )


def _claims(
    token: str,
    *,
    settings: Settings,
) -> _ReviewClaims:
    try:
        payload = _serializer(settings).loads(
            token,
            max_age=REVIEW_MAX_AGE_SECONDS,
        )
        return _ReviewClaims.model_validate(payload)
    except SignatureExpired as exc:
        raise LicensedStateReviewError(
            "This impact review expired. Review the changes again."
        ) from exc
    except (BadSignature, ValueError) as exc:
        raise LicensedStateReviewError(
            "This impact review is invalid. Review the changes again."
        ) from exc


def _apply_request_impact(
    db: Session,
    *,
    item: LeadRequest,
    impact: LicensedStateImpact,
    added_states: list[str],
    removed_states: list[str],
) -> None:
    item.states_snapshot = impact.resulting_states
    if impact.action == "narrowed":
        item.status_message = (
            "Licensed States changed. Removed "
            f"{', '.join(removed_states)}; request now covers "
            f"{', '.join(impact.resulting_states)}. "
            "Existing approval remains valid."
        )
    else:
        item.status = RequestStatus.canceled.value
        item.status_message = (
            "Canceled because no requested states remain in the "
            "Customer's Licensed States."
        )
        item.closed_at = utcnow()
    enqueue_job(
        db,
        "licensed_states_changed",
        item.id,
        {
            "added": added_states,
            "removed": removed_states,
            "requestAction": impact.action,
            "states": impact.resulting_states,
        },
    )


def apply_review(
    db: Session,
    *,
    user_id: uuid.UUID,
    confirmation: LicensedStateConfirmation,
    settings: Settings,
) -> LicensedStateApplyResult:
    claims = _claims(confirmation.review_token, settings=settings)
    if claims.user_id != user_id:
        raise LicensedStateReviewError(
            "This impact review belongs to another account."
        )

    profile = db.scalar(
        select(CustomerProfile)
        .where(CustomerProfile.user_id == user_id)
        .with_for_update()
    )
    if profile is None:
        raise LookupError("Profile was not found.")
    if _version(profile) != claims.version:
        raise LicensedStateConflict(
            "Licensed States changed in another session. "
            "Review the latest impact before saving."
        )

    requests, impacts = _affected_requests(
        db,
        user_id=user_id,
        proposed_states=claims.proposed_states,
        lock=True,
    )
    if _impact_checksum(impacts) != claims.impact_checksum:
        raise LicensedStateConflict(
            "A Batch Request changed after this review. "
            "Review the latest impact before saving."
        )

    current_states = normalize_states(profile.licensed_states)
    proposed_states = claims.proposed_states
    added_states = sorted(set(proposed_states) - set(current_states))
    removed_states = sorted(set(current_states) - set(proposed_states))
    impacts_by_id = {impact.request_id: impact for impact in impacts}
    for item in requests:
        impact = impacts_by_id.get(item.id)
        if impact is not None:
            _apply_request_impact(
                db,
                item=item,
                impact=impact,
                added_states=added_states,
                removed_states=removed_states,
            )

    profile.licensed_states = proposed_states
    if profile.customer is not None:
        profile.customer.licensed_states = proposed_states
    profile.updated_at = utcnow()
    record_activity(
        db,
        action="licensed_states_update",
        target_type="customer",
        target_id=profile.customer_id or profile.user_id,
        actor_id=user_id,
        reason="Customer confirmed Licensed State impact review.",
        details={
            "before": current_states,
            "after": proposed_states,
            "added": added_states,
            "removed": removed_states,
            "requestUpdates": [
                {
                    "requestId": str(impact.request_id),
                    "action": impact.action,
                    "before": impact.current_states,
                    "after": impact.resulting_states,
                }
                for impact in impacts
            ],
        },
    )
    db.flush()

    result = LicensedStateApplyResult(
        account=workspace(profile),
        overview=build_customer_overview(
            db,
            user_id=user_id,
            profile=profile,
        ),
        requests=build_request_workspace(
            db,
            user_id=user_id,
            profile=profile,
        ),
    )
    db.commit()
    return result
