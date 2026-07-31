from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from .fulfillment import artifact_available
from .models import (
    AuditEntry,
    CustomerProfile,
    DistributionEvent,
    LeadDispositionTransition,
    LeadOutcome,
    LeadRequest,
    RequestStatus,
)
from .schemas import (
    CustomerOverviewAction,
    CustomerOverviewItem,
    CustomerOverviewOut,
    CustomerOverviewStatus,
)
from .states import normalize_states


_REQUEST_STATUS: dict[str, CustomerOverviewStatus] = {
    RequestStatus.pending.value: CustomerOverviewStatus(
        label="Submitted",
        description="Your request is ready for review.",
        tone="info",
    ),
    RequestStatus.approved.value: CustomerOverviewStatus(
        label="Under Review",
        description="Your request has been approved and is being prepared.",
        tone="info",
    ),
    RequestStatus.processing.value: CustomerOverviewStatus(
        label="Preparing Batch",
        description="We are preparing your leads for delivery.",
        tone="warning",
    ),
    RequestStatus.waiting_inventory.value: CustomerOverviewStatus(
        label="Preparing Batch",
        description=(
            "We are waiting for enough matching leads. "
            "There is nothing you need to do."
        ),
        tone="warning",
    ),
    RequestStatus.generated.value: CustomerOverviewStatus(
        label="Preparing Batch",
        description="Your Batch is prepared and being delivered.",
        tone="warning",
    ),
    RequestStatus.delivered.value: CustomerOverviewStatus(
        label="Delivered",
        description="Your Batch has been delivered.",
        tone="success",
    ),
    RequestStatus.rejected.value: CustomerOverviewStatus(
        label="Not Approved",
        description="This request was not approved. You can submit a new request.",
        tone="danger",
    ),
    RequestStatus.canceled.value: CustomerOverviewStatus(
        label="Canceled",
        description="This request was canceled. You can submit a new request.",
        tone="neutral",
    ),
    RequestStatus.failed.value: CustomerOverviewStatus(
        label="Needs Attention",
        description=(
            "We could not complete this request. "
            "Please contact Jawnix before trying again."
        ),
        tone="danger",
    ),
}


ARTIFACT_EXPIRING_SOON = timedelta(days=7)


def customer_request_status(status: str) -> CustomerOverviewStatus:
    """Translate fulfillment state into the public Customer vocabulary."""

    return _REQUEST_STATUS.get(
        status,
        CustomerOverviewStatus(
            label="Under Review",
            description="We are reviewing your request. There is nothing you need to do.",
            tone="info",
        ),
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def build_customer_overview(
    db: Session,
    *,
    user_id: uuid.UUID,
    profile: CustomerProfile,
    now: datetime | None = None,
) -> CustomerOverviewOut:
    """Build only the work that currently needs this Customer."""

    moment = now or datetime.now(timezone.utc)
    requests = list(
        db.scalars(
            select(LeadRequest)
            .where(LeadRequest.user_id == user_id)
            .order_by(LeadRequest.created_at.desc(), LeadRequest.id.desc())
        )
    )
    downloaded = set(
        db.scalars(
            select(AuditEntry.target_id)
            .where(
                AuditEntry.action == "batch_artifact_downloaded",
                AuditEntry.actor_user_id == str(user_id),
            )
        )
    )
    distributed_request_ids = set(
        db.scalars(
            select(DistributionEvent.request_id).where(
                DistributionEvent.request_id.in_([item.id for item in requests])
            )
        )
    )
    feedback_request_ids = set(
        db.scalars(
            select(DistributionEvent.request_id)
            .join(
                LeadDispositionTransition,
                LeadDispositionTransition.distribution_event_id
                == DistributionEvent.id,
            )
            .where(DistributionEvent.request_id.is_not(None))
        )
    )
    feedback_request_ids.update(
        db.scalars(
            select(DistributionEvent.request_id)
            .join(
                LeadOutcome,
                LeadOutcome.distribution_event_id == DistributionEvent.id,
            )
            .where(DistributionEvent.request_id.is_not(None))
        )
    )

    items: list[CustomerOverviewItem] = []
    if not normalize_states(profile.licensed_states):
        items.append(
            CustomerOverviewItem(
                id="setup-problem:no-licensed-states",
                kind="setup_problem",
                title="Add Licensed States",
                description=(
                    "Add at least one Licensed State before requesting a Batch."
                ),
                tone="warning",
                action=CustomerOverviewAction(
                    kind="add_licensed_states",
                    label="Open Account",
                    description="Add the states where you are licensed.",
                    href="/app/account",
                ),
            )
        )
    for request in requests:
        if request.status == RequestStatus.waiting_inventory.value:
            states = ", ".join(normalize_states(request.states_snapshot))
            items.append(
                CustomerOverviewItem(
                    id=f"waiting-inventory:{request.id}",
                    kind="waiting_inventory",
                    title="Batch Request is waiting for inventory",
                    description=(
                        f"Review or cancel your {request.lead_count:,}-lead "
                        f"request for {states}."
                    ),
                    tone="warning",
                    action=CustomerOverviewAction(
                        kind="review_request",
                        label="Review request",
                        description="Open this Batch Request's detail page.",
                        href=f"/app/requests?request={request.id}",
                    ),
                )
            )
            continue
        if request.status != RequestStatus.delivered.value:
            continue

        artifact = request.artifact
        if str(request.id) not in downloaded:
            if (
                artifact is None
                or not artifact_available(artifact, now=moment)
                or artifact.expires_at is None
            ):
                continue
            remaining = _as_utc(artifact.expires_at) - moment
            if remaining <= ARTIFACT_EXPIRING_SOON:
                days = max(1, ceil(remaining / timedelta(days=1)))
                items.append(
                    CustomerOverviewItem(
                        id=f"artifact-expiring:{request.id}",
                        kind="artifact_expiring",
                        title="Batch Artifact expires soon",
                        description=(
                            f"Download the {request.lead_count:,}-lead Batch "
                            f"Artifact within {days} "
                            f"{('day' if days == 1 else 'days')}."
                        ),
                        tone="warning",
                        action=CustomerOverviewAction(
                            kind="download_artifact",
                            label="Download CSV",
                            description=(
                                "Download this Batch Artifact before it expires."
                            ),
                            href=(
                                f"/api/me/batch-requests/{request.id}/artifact"
                            ),
                        ),
                    )
                )
                continue
            items.append(
                CustomerOverviewItem(
                    id=f"batch-ready:{request.id}",
                    kind="batch_ready",
                    title="Your Batch is ready",
                    description=(
                        f"Download the {request.lead_count:,}-lead Batch Artifact."
                    ),
                    tone="info",
                    action=CustomerOverviewAction(
                        kind="download_artifact",
                        label="Download CSV",
                        description="Download this Batch Artifact while it is live.",
                        href=f"/api/me/batch-requests/{request.id}/artifact",
                    ),
                )
            )
            continue

        if (
            request.id in distributed_request_ids
            and request.id not in feedback_request_ids
        ):
            items.append(
                CustomerOverviewItem(
                    id=f"feedback-nudge:{request.id}",
                    kind="feedback_nudge",
                    title="How did this Batch perform?",
                    description=(
                        f"Share one lead outcome from this "
                        f"{request.lead_count:,}-lead Batch."
                    ),
                    tone="info",
                    action=CustomerOverviewAction(
                        kind="submit_feedback",
                        label="Give feedback",
                        description=(
                            "Record one Lead Disposition or Quality Rating."
                        ),
                        href=f"/app/feedback?request={request.id}",
                    ),
                )
            )
    return CustomerOverviewOut(items=items)
