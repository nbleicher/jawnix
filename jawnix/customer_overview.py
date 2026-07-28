from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CustomerProfile, LeadRequest, RequestStatus
from .schemas import (
    CustomerOverviewAction,
    CustomerOverviewDelivery,
    CustomerOverviewOut,
    CustomerOverviewRequest,
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


_REQUEST_BATCH = CustomerOverviewAction(
    kind="request_batch",
    label="Request a Batch",
    description="Choose a quantity and Licensed States for your next Batch.",
    href="/app/requests",
)
_SUBMIT_FEEDBACK = CustomerOverviewAction(
    kind="submit_feedback",
    label="Submit Lead Feedback",
    description="Share the result of a delivered lead.",
    href="/app/feedback",
)


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


def _request_out(item: LeadRequest) -> CustomerOverviewRequest:
    return CustomerOverviewRequest(
        id=item.id,
        lead_count=item.lead_count,
        states=normalize_states(item.states_snapshot),
        submitted_at=item.created_at,
        delivered_at=item.delivered_at,
        status=customer_request_status(item.status),
    )


def _next_action(
    profile: CustomerProfile,
    current_request: LeadRequest | None,
) -> CustomerOverviewAction:
    if profile.mapping_confirmed_at is None or profile.customer_id is None:
        return CustomerOverviewAction(
            kind="review_account",
            label="Review Account",
            description=(
                "Review your account details while an administrator confirms "
                "your Customer mapping."
            ),
            href="/app/account",
        )
    if not normalize_states(profile.licensed_states):
        return CustomerOverviewAction(
            kind="add_licensed_states",
            label="Add Licensed States",
            description="Add at least one Licensed State before requesting a Batch.",
            href="/app/account",
        )
    if current_request is None:
        return _REQUEST_BATCH
    if current_request.status == RequestStatus.delivered.value:
        return _SUBMIT_FEEDBACK
    if current_request.status in {
        RequestStatus.rejected.value,
        RequestStatus.canceled.value,
    }:
        return _REQUEST_BATCH
    if current_request.status == RequestStatus.failed.value:
        return CustomerOverviewAction(
            kind="review_request",
            label="Review Request",
            description="Review the request outcome and contact Jawnix for help.",
            href="/app/requests",
        )
    return CustomerOverviewAction(
        kind="review_request",
        label="Review Request",
        description="See the latest progress for your current Batch Request.",
        href="/app/requests",
    )


def build_customer_overview(
    db: Session,
    *,
    user_id: uuid.UUID,
    profile: CustomerProfile,
    delivery_limit: int = 3,
) -> CustomerOverviewOut:
    """Build the one Customer-workspace aggregate used by routed screens."""

    current_request = db.scalar(
        select(LeadRequest)
        .where(LeadRequest.user_id == user_id)
        .order_by(LeadRequest.created_at.desc(), LeadRequest.id.desc())
        .limit(1)
    )
    delivered_requests = list(
        db.scalars(
            select(LeadRequest)
            .where(
                LeadRequest.user_id == user_id,
                LeadRequest.delivered_at.is_not(None),
            )
            .order_by(LeadRequest.delivered_at.desc(), LeadRequest.id.desc())
            .limit(delivery_limit)
        )
    )
    deliveries = [
        CustomerOverviewDelivery(
            request_id=item.id,
            lead_count=item.lead_count,
            states=normalize_states(item.states_snapshot),
            delivered_at=item.delivered_at,
        )
        for item in delivered_requests
        if item.delivered_at is not None
    ]
    return CustomerOverviewOut(
        first_name=profile.first_name.strip(),
        licensed_states=normalize_states(profile.licensed_states),
        current_request=(
            _request_out(current_request)
            if current_request is not None
            else None
        ),
        recent_deliveries=deliveries,
        next_action=_next_action(profile, current_request),
        primary_actions=[_REQUEST_BATCH, _SUBMIT_FEEDBACK],
    )
