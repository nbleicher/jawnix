"""The guided Batch Request flow and the Customer milestone graph.

Two things live here that the Customer application must never have to derive
for itself.

The **milestone graph** collapses the nine internal fulfillment statuses onto
the four milestones a Customer was promised — Submitted, Under Review,
Preparing Batch, Delivered — and says, in data, exactly what each node means.
Every distinction a screen needs is a value on the node, so the graph can be
drawn horizontally, vertically, or read aloud without any of them disagreeing
and without motion carrying meaning.

**Submission** is idempotent. The client mints one key per guided flow, so a
double-click, a retried POST, or a reloaded review stage all resolve to the one
Batch Request the first attempt created.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .billing import (
    BillingError,
    place_batch_hold,
    prepare_submission,
    release_batch_hold,
)
from .config import get_settings
from .customer_overview import customer_request_status
from .fulfillment import artifact_available
from .jobs import enqueue_job
from .models import Customer, CustomerProfile, LeadRequest, RequestStatus, utcnow
from .schemas import (
    CustomerBatchArtifact,
    CustomerMilestone,
    CustomerOverviewAction,
    CustomerRequestAction,
    CustomerRequestBlocker,
    CustomerRequestCreate,
    CustomerRequestDetail,
    CustomerRequestLimits,
    CustomerRequestMilestones,
    CustomerRequestOutcome,
    CustomerRequestPause,
    CustomerRequestWorkspaceOut,
)
from .states import normalize_states


# The Batch Request domain bound: an exact quantity of 1 to 100,000 Leads.
MINIMUM_LEAD_COUNT = 1
MAXIMUM_LEAD_COUNT = 100_000

# A Batch Request may be withdrawn only before any Distribution Event commits.
# Allocation commits distribution, so these three statuses are the whole window
# in which cancellation is a real option rather than a button that 409s.
CANCELABLE_STATUSES = frozenset(
    {
        RequestStatus.pending.value,
        RequestStatus.approved.value,
        RequestStatus.waiting_inventory.value,
    }
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


class RequestSubmissionError(Exception):
    """A guided submission the domain refuses."""

    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _support_action() -> CustomerRequestAction:
    return CustomerRequestAction(
        kind="contact_support",
        label="Contact Jawnix",
        description="Send us this request and we will pick it up from here.",
        href=f"mailto:{get_settings().support_email}",
    )


_REQUEST_AGAIN = CustomerRequestAction(
    kind="request_batch",
    label="Request another Batch",
    description="Start a new request with the quantity and states you want.",
    href="/app/requests",
)
_SUBMIT_FEEDBACK = CustomerRequestAction(
    kind="submit_feedback",
    label="Submit Lead Feedback",
    description="Tell us how the leads in this Batch worked out.",
    href="/app/feedback",
)


def request_limits(profile: CustomerProfile) -> CustomerRequestLimits:
    """The bounds the quantity and scope stages validate against."""

    return CustomerRequestLimits(
        minimum_lead_count=MINIMUM_LEAD_COUNT,
        maximum_lead_count=MAXIMUM_LEAD_COUNT,
        licensed_states=normalize_states(profile.licensed_states),
    )


def request_blocker(profile: CustomerProfile) -> CustomerRequestBlocker | None:
    """Why the guided flow cannot start, or None when it can.

    A blocker is not a validation message. It means the Customer has nothing
    valid to enter yet, so the screen offers the fix instead of the stages.
    """

    if profile.mapping_confirmed_at is None or profile.customer_id is None:
        return CustomerRequestBlocker(
            reason="mapping_unconfirmed",
            label="Your account is still being set up",
            description=(
                "An administrator is confirming your Customer mapping. "
                "You can request a Batch as soon as that is done."
            ),
            action=CustomerOverviewAction(
                kind="review_account",
                label="Review Account",
                description="Check the account details we have for you.",
                href="/app/account",
            ),
        )
    if not normalize_states(profile.licensed_states):
        return CustomerRequestBlocker(
            reason="no_licensed_states",
            label="Add a Licensed State first",
            description=(
                "A Batch Request covers the states you are licensed in. "
                "Add at least one before requesting a Batch."
            ),
            action=CustomerOverviewAction(
                kind="add_licensed_states",
                label="Add Licensed States",
                description="Save the states you are licensed in.",
                href="/app/account",
            ),
        )
    return None


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


def _next_action(item: LeadRequest) -> CustomerRequestAction | None:
    if item.status == RequestStatus.delivered.value:
        return _SUBMIT_FEEDBACK
    if item.status == RequestStatus.failed.value:
        return _support_action()
    if item.status in {
        RequestStatus.rejected.value,
        RequestStatus.canceled.value,
    }:
        return _REQUEST_AGAIN
    return None


def request_detail(item: LeadRequest) -> CustomerRequestDetail:
    """One Batch Request as the Customer application reads it."""

    artifact = item.artifact if item.delivered_at is not None else None
    customer_artifact = None
    if artifact is not None:
        available = artifact_available(artifact)
        customer_artifact = CustomerBatchArtifact(
            filename=artifact.filename,
            row_count=artifact.row_count,
            parts=artifact.parts,
            expires_at=artifact.expires_at,
            available=available,
            download_href=(
                f"/api/me/batch-requests/{item.id}/artifact"
                if available
                else None
            ),
        )

    return CustomerRequestDetail(
        id=item.id,
        lead_count=item.lead_count,
        rows_per_file=item.rows_per_file,
        states=normalize_states(item.states_snapshot),
        submitted_at=item.created_at,
        delivered_at=item.delivered_at,
        status=customer_request_status(item.status),
        milestones=build_milestones(item),
        can_cancel=item.status in CANCELABLE_STATUSES,
        next_action=_next_action(item),
        receipt_href=f"/app/requests?request={item.id}",
        artifact=customer_artifact,
    )


def build_request_workspace(
    db: Session,
    *,
    user_id: uuid.UUID,
    profile: CustomerProfile,
    history_limit: int = 20,
) -> CustomerRequestWorkspaceOut:
    """Everything the guided Batch Request screen renders in one read."""

    items = list(
        db.scalars(
            select(LeadRequest)
            .where(LeadRequest.user_id == user_id)
            .order_by(LeadRequest.created_at.desc(), LeadRequest.id.desc())
            .limit(history_limit)
        )
    )
    return CustomerRequestWorkspaceOut(
        limits=request_limits(profile),
        blocker=request_blocker(profile),
        requests=[request_detail(item) for item in items],
    )


def _requested_states(
    payload: CustomerRequestCreate,
    licensed_states: list[str],
) -> list[str]:
    """The scope stage, revalidated against Licensed States on the server.

    The guided flow blocks an unlicensed scope before review; this is the same
    rule enforced where it is authoritative, so a request that skipped the
    stages cannot get past it either.
    """

    if payload.state_mode == "all_saved":
        return licensed_states
    unlicensed = sorted(set(payload.states) - set(licensed_states))
    if unlicensed:
        raise RequestSubmissionError(
            "These are not Licensed States on your account: "
            f"{', '.join(unlicensed)}."
        )
    return normalize_states(payload.states)


def _existing_submission(
    db: Session,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
) -> LeadRequest | None:
    return db.scalar(
        select(LeadRequest).where(
            LeadRequest.user_id == user_id,
            LeadRequest.idempotency_key == idempotency_key,
        )
    )


def submit_request(
    db: Session,
    *,
    user_id: uuid.UUID,
    profile: CustomerProfile,
    payload: CustomerRequestCreate,
) -> tuple[LeadRequest, bool]:
    """Create the Batch Request, or return the one this key already created.

    Returns the request and whether this call is the one that created it. The
    unique key does the real work: two concurrent submissions race to insert,
    the loser catches the integrity error, and both callers leave with the same
    Batch Request.
    """

    existing = _existing_submission(
        db,
        user_id=user_id,
        idempotency_key=payload.idempotency_key,
    )
    if existing is not None:
        return existing, False

    blocker = request_blocker(profile)
    if blocker is not None:
        raise RequestSubmissionError(blocker.description, status_code=409)
    if (
        profile.customer is None
        or not profile.customer.active
        or profile.customer.deleted_at is not None
        or (
            profile.customer.agency is not None
            and (
                not profile.customer.agency.active
                or profile.customer.agency.deleted_at is not None
            )
        )
    ):
        raise RequestSubmissionError(
            "Your distribution mapping must be active and confirmed before "
            "requesting a Batch.",
            status_code=409,
        )

    licensed_states = normalize_states(profile.licensed_states)
    states = _requested_states(payload, licensed_states)

    # Serialize duplicate detection with wallet/hold changes. A concurrent
    # first submission may have been invisible at the optimistic check above
    # and committed while this caller waited for the Customer lock.
    db.scalar(
        select(Customer)
        .where(Customer.id == profile.customer_id)
        .with_for_update()
    )
    existing = _existing_submission(
        db,
        user_id=user_id,
        idempotency_key=payload.idempotency_key,
    )
    if existing is not None:
        return existing, False

    try:
        billing = prepare_submission(
            db,
            customer_id=profile.customer_id,
            lead_count=payload.lead_count,
        )
    except BillingError as exc:
        raise RequestSubmissionError(
            exc.detail,
            status_code=exc.status_code,
        ) from None

    item = LeadRequest(
        user_id=user_id,
        agent_id=profile.customer_id,
        lead_count=payload.lead_count,
        rows_per_file=payload.rows_per_file or payload.lead_count,
        state_mode=payload.state_mode,
        states_snapshot=states,
        delivery_email=profile.email,
        status=RequestStatus.pending.value,
        status_message="Awaiting Telegram approval.",
        idempotency_key=payload.idempotency_key,
    )
    db.add(item)
    place_batch_hold(db, request=item, billing=billing)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raced = _existing_submission(
            db,
            user_id=user_id,
            idempotency_key=payload.idempotency_key,
        )
        if raced is None:
            raise
        return raced, False
    enqueue_job(db, "notify_request", item.id)
    db.commit()
    db.refresh(item)
    return item, True


def cancel_request(
    db: Session,
    *,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    message: str = "Canceled by customer.",
) -> LeadRequest:
    """Withdraw a Batch Request that has not committed any distribution yet."""

    item = db.scalar(
        select(LeadRequest)
        .where(
            LeadRequest.id == request_id,
            LeadRequest.user_id == user_id,
        )
        .with_for_update()
    )
    if item is None:
        raise RequestSubmissionError("Request was not found.", status_code=404)
    if item.status not in CANCELABLE_STATUSES:
        raise RequestSubmissionError(
            "This request has already started delivery and can no longer be "
            "canceled.",
            status_code=409,
        )
    item.status = RequestStatus.canceled.value
    item.status_message = message
    item.closed_at = utcnow()
    release_batch_hold(db, item)
    enqueue_job(db, "update_notification", item.id)
    db.commit()
    db.refresh(item)
    return item
