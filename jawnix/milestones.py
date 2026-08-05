"""The Batch Request milestone graph shared by Customer and Administrator views.

Collapses the nine internal fulfillment statuses onto the four milestones a
Customer was promised — Submitted, Under Review, Preparing Batch, Delivered —
and says, in data, exactly what each node means. Extracted from
``customer_requests`` so ``fulfillment.describe_request`` can project the same
graph without a circular import.
"""

from __future__ import annotations

from datetime import datetime

from .models import LeadRequest, RequestStatus
from .schemas import (
    CustomerMilestone,
    CustomerRequestMilestones,
    CustomerRequestOutcome,
    CustomerRequestPause,
)


_TERMINAL_OUTCOMES = {
    RequestStatus.rejected.value: "rejected",
    RequestStatus.canceled.value: "canceled",
    RequestStatus.failed.value: "failed",
}

# Reaching Under Review or later is inferable from the status alone even when a
# timestamp is missing, which keeps the graph honest against rows written
# before a column existed or by a path that skipped a stamp.
_AFTER_REVIEW = frozenset(
    {
        RequestStatus.processing.value,
        RequestStatus.waiting_inventory.value,
        RequestStatus.generated.value,
        RequestStatus.delivered.value,
    }
)


def _reached(item: LeadRequest) -> list[tuple[str, bool, datetime | None]]:
    """Each milestone with whether the request reached it, and when.

    Status and timestamp are both consulted so a milestone counts as reached
    when either says so.
    """

    status = item.status
    reached_review = (
        item.approved_at is not None or status in _AFTER_REVIEW
    )
    reached_preparing = (
        item.processed_at is not None or status in _AFTER_REVIEW
    )
    reached_delivered = (
        item.delivered_at is not None
        or status == RequestStatus.delivered.value
    )
    return [
        ("submitted", True, item.created_at),
        ("under_review", reached_review, item.approved_at),
        ("preparing_batch", reached_preparing, item.processed_at),
        ("delivered", reached_delivered, item.delivered_at),
    ]


_MILESTONE_COPY: dict[str, tuple[str, str]] = {
    "submitted": (
        "Submitted",
        "We have your request for this quantity and these states.",
    ),
    "under_review": (
        "Under Review",
        "Jawnix is checking the quantity and states you asked for.",
    ),
    "preparing_batch": (
        "Preparing Batch",
        "We are selecting your leads and building your file.",
    ),
    "delivered": (
        "Delivered",
        "Your Batch is available in the customer portal.",
    ),
}

_WAITING_DESCRIPTION = (
    "We are holding this request until enough matching leads are available. "
    "Nothing has gone wrong and there is nothing you need to do — it moves on "
    "automatically as soon as inventory covers the full quantity."
)

_OUTCOME_COPY: dict[str, tuple[str, str, str]] = {
    "rejected": (
        "Not Approved",
        "This request was not approved, so no leads were reserved for it. "
        "You can submit a new request with a different quantity or scope.",
        "danger",
    ),
    "canceled": (
        "Canceled",
        "This request was withdrawn before any leads were reserved. "
        "You can submit a new request whenever you are ready.",
        "neutral",
    ),
    "failed": (
        "Needs Attention",
        "We could not finish this request. Please contact Jawnix so we can "
        "sort it out — do not submit a duplicate request.",
        "danger",
    ),
}


def build_milestones(item: LeadRequest) -> CustomerRequestMilestones:
    """The Customer-facing journey for one Batch Request.

    The furthest milestone the request reached is where the story is: it is
    `current` while the request is moving, `paused` while it waits for
    inventory, and `stopped` when the request ended there. Nothing after it is
    ever marked `upcoming`, because a stopped request will not arrive.
    """

    progress = _reached(item)
    last_reached = max(
        index for index, (_, reached, _) in enumerate(progress) if reached
    )
    outcome_kind = _TERMINAL_OUTCOMES.get(item.status)
    waiting = item.status == RequestStatus.waiting_inventory.value
    finished = item.status == RequestStatus.delivered.value

    milestones: list[CustomerMilestone] = []
    for index, (key, _, occurred_at) in enumerate(progress):
        label, description = _MILESTONE_COPY[key]
        if index < last_reached:
            state = "complete"
        elif index == last_reached:
            if outcome_kind is not None:
                state = "stopped"
            elif waiting:
                state = "paused"
                description = _WAITING_DESCRIPTION
            elif finished:
                state = "complete"
            else:
                state = "current"
        else:
            state = "not_reached" if outcome_kind is not None else "upcoming"
        milestones.append(
            CustomerMilestone(
                key=key,
                label=label,
                description=description,
                state=state,
                occurred_at=occurred_at,
            )
        )

    stopped_key = milestones[last_reached].key
    outcome = None
    if outcome_kind is not None:
        label, description, tone = _OUTCOME_COPY[outcome_kind]
        outcome = CustomerRequestOutcome(
            kind=outcome_kind,
            milestone_key=stopped_key,
            label=label,
            description=description,
            tone=tone,
            occurred_at=item.closed_at,
        )
    return CustomerRequestMilestones(
        milestones=milestones,
        current_key=None if outcome_kind is not None else stopped_key,
        pause=(
            CustomerRequestPause(
                kind="inventory_wait",
                milestone_key=stopped_key,
                label="Waiting for Inventory",
                description=_WAITING_DESCRIPTION,
            )
            if waiting
            else None
        ),
        outcome=outcome,
    )
