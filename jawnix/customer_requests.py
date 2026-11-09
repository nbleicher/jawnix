"""The guided Batch Request flow and the Customer milestone graph.

Two things live here that the Customer application must never have to derive
for itself.

The **milestone graph** collapses the nine internal fulfillment statuses onto
the four milestones a Customer was promised — Submitted, Approved,
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
from datetime import datetime, timezone
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement
from .customer_history import request_history_scope

from .batch_copies import can_ask_for_copy, pending_copy_ids
from .billing import (
    BillingError,
    place_batch_hold,
    prepare_submission,
    release_batch_hold,
)
from .config import get_settings
from .customer_overview import ARTIFACT_EXPIRING_SOON, customer_request_status
from .fulfillment import artifact_available, artifact_download_filename
from .jobs import enqueue_job
from .milestones import build_milestones
from .models import (
    AuditEntry,
    Customer,
    CustomerProfile,
    DistributionEvent,
    LeadRequest,
    RequestStatus,
    utcnow,
)
from .schemas import (
    CustomerBatchArtifact,
    CustomerBatchCost,
    CustomerBatchEvent,
    CustomerBatchStateCount,
    CustomerOverviewAction,
    CustomerRequestAction,
    CustomerRequestBlocker,
    CustomerRequestCreate,
    CustomerRequestDetail,
    CustomerRequestHistoryPage,
    CustomerRequestLimits,
    CustomerRequestWorkspaceOut,
)
from .states import US_STATE_OPTIONS, normalize_states


# The Batch Request domain bound: an exact quantity of 1 to 100,000 Leads.
MINIMUM_LEAD_COUNT = 1
MAXIMUM_LEAD_COUNT = 100_000

# Request history is read a page at a time, newest first. Every page is
# reachable, so no request ever falls out of the Customer's history.
REQUEST_HISTORY_PAGE_SIZE = 20

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
    label="New request",
    description="Start a new request with the quantity and states you want.",
    href="/app/new-request",
)

_STATE_NAMES = dict(US_STATE_OPTIONS)

def _csv_columns(csv_format: str) -> list[str]:
    """The delivered file's columns, from the layout it was written with."""

    from .allocation import CSV_FORMATS

    return list(CSV_FORMATS.get(csv_format, ()))


def customer_batch_href(item: LeadRequest) -> str:
    """The batch page in the Customer portal: "/app/batches/1042"."""

    return f"/app/batches/{item.number}"


def request_limits(profile: CustomerProfile) -> CustomerRequestLimits:
    """The bounds the request form validates against."""

    states = normalize_states(profile.licensed_states)
    return CustomerRequestLimits(
        minimum_lead_count=MINIMUM_LEAD_COUNT,
        maximum_lead_count=MAXIMUM_LEAD_COUNT,
        licensed_states=states,
        state_names={state: _STATE_NAMES.get(state, state) for state in states},
    )


def request_blocker(profile: CustomerProfile) -> CustomerRequestBlocker | None:
    """Why the request form cannot start, or None when it can.

    A blocker is not a validation message. It means the Customer has nothing
    valid to enter yet, so the screen offers the fix instead of the form.
    """

    if profile.mapping_confirmed_at is None or profile.customer_id is None:
        return CustomerRequestBlocker(
            reason="mapping_unconfirmed",
            label="Your account is still being set up",
            description=(
                "We are finishing your account setup. "
                "You can request a batch as soon as that is done."
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
            label="Add your appointed states first",
            description=(
                "A batch covers the states you are appointed in. "
                "Add at least one before you request a batch."
            ),
            action=CustomerOverviewAction(
                kind="add_licensed_states",
                label="Add appointed states",
                description="Save the states you are appointed in.",
                href="/app/account/states",
            ),
        )
    return None


def _next_action(item: LeadRequest) -> CustomerRequestAction | None:
    if item.status == RequestStatus.failed.value:
        return _support_action()
    if item.status in {
        RequestStatus.rejected.value,
        RequestStatus.canceled.value,
    }:
        return _REQUEST_AGAIN
    return None


_PREPARING_STATUSES = frozenset(
    {
        RequestStatus.approved.value,
        RequestStatus.processing.value,
        RequestStatus.waiting_inventory.value,
        RequestStatus.generated.value,
    }
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _stage(
    item: LeadRequest,
    *,
    copy_pending: bool,
    downloaded: bool,
    now: datetime,
) -> str:
    """Where the batch is, in the Customer's words (``CustomerBatchStage``)."""

    status = item.status
    if status == RequestStatus.pending.value:
        return "waiting_approval"
    if status in _PREPARING_STATUSES:
        return "preparing"
    if status == RequestStatus.rejected.value:
        return "not_approved"
    if status == RequestStatus.canceled.value:
        return "canceled"
    if status == RequestStatus.failed.value:
        return "needs_attention"
    artifact = item.artifact
    if artifact is None:
        return "delivered"
    if artifact_available(artifact, now=now):
        # Red only when there is something to do: a file never downloaded.
        expires_at = _as_utc(artifact.expires_at)
        soon = expires_at is not None and expires_at - now <= ARTIFACT_EXPIRING_SOON
        return "expiring" if soon and not downloaded else "ready"
    return "copy_on_the_way" if copy_pending else "file_expired"


def _cost(item: LeadRequest) -> CustomerBatchCost | None:
    if not item.is_billed:
        return None
    hold = item.billing_hold
    state = None
    at = None
    if hold is not None:
        if hold.status == "active":
            state, at = "held", hold.created_at
        elif hold.status == "captured":
            state, at = "charged", hold.captured_at
        elif hold.status == "released":
            state, at = "released", hold.released_at
    return CustomerBatchCost(
        rate_cents_per_thousand=item.lead_rate_cents_per_thousand,
        amount_cents=item.billing_amount_cents,
        state=state,
        at=at,
    )


def _history(
    item: LeadRequest,
    *,
    copy_pending: bool,
    now: datetime,
) -> list[CustomerBatchEvent]:
    """The batch's history, oldest first, with what is still to come last."""

    def event(key: str, label: str, at: datetime | None, done: bool = True):
        return CustomerBatchEvent(key=key, label=label, at=at, done=done)

    status = item.status
    approved = item.approved_at is not None or status in (
        _PREPARING_STATUSES | {RequestStatus.delivered.value}
    )
    history = [event("submitted", "Submitted", item.created_at)]
    if approved:
        history.append(event("approved", "Approved", item.approved_at))
    if status == RequestStatus.rejected.value:
        history.append(event("not_approved", "Not approved", item.closed_at))
    elif status == RequestStatus.canceled.value:
        history.append(event("canceled", "Canceled", item.closed_at))
    elif status == RequestStatus.failed.value:
        history.append(event("needs_attention", "Needs attention", item.closed_at))
    elif status != RequestStatus.delivered.value:
        if not approved:
            history.append(event("approved", "Approved", None, done=False))
        history.append(event("delivered", "Ready to download", None, done=False))
    else:
        history.append(event("delivered", "Delivered", item.delivered_at))
        artifact = item.artifact
        if artifact is not None:
            made = _as_utc(artifact.created_at)
            delivered = _as_utc(item.delivered_at)
            # The first file is made before delivery; a later one is a copy.
            if made is not None and delivered is not None and made > delivered:
                history.append(event("copy_ready", "New copy ready", made))
            if not artifact_available(artifact, now=now):
                expires_at = _as_utc(artifact.expires_at)
                history.append(
                    event(
                        "file_expired",
                        "File expired",
                        expires_at if expires_at is not None and expires_at <= now else None,
                    )
                )
        if copy_pending:
            history.append(
                event("copy_on_the_way", "New copy on the way", None, done=False)
            )
    return history


def request_detail(
    item: LeadRequest,
    *,
    copy_pending: bool = False,
    downloaded: bool = False,
    leads_by_state: list[CustomerBatchStateCount] | None = None,
    now: datetime | None = None,
) -> CustomerRequestDetail:
    """One Batch Request as the Customer application reads it.

    ``copy_pending`` and ``downloaded`` are read in bulk by the callers that
    have them (``customer_batches``); without them a batch reads as having no
    copy on the way and as downloaded, which never shows a false alarm.
    """

    moment = now or datetime.now(timezone.utc)
    artifact = item.artifact if item.delivered_at is not None else None
    customer_artifact = None
    if artifact is not None:
        available = artifact_available(artifact, now=moment)
        customer_artifact = CustomerBatchArtifact(
            filename=artifact_download_filename(artifact),
            row_count=artifact.row_count,
            parts=artifact.parts,
            columns=_csv_columns(artifact.csv_format),
            expires_at=artifact.expires_at,
            available=available,
            download_href=(
                f"/api/me/batch-requests/{item.id}/artifact"
                if available
                else None
            ),
        )

    href = customer_batch_href(item)
    return CustomerRequestDetail(
        id=item.id,
        number=item.number,
        lead_count=item.lead_count,
        rows_per_file=item.rows_per_file,
        states=normalize_states(item.states_snapshot),
        submitted_at=item.created_at,
        delivered_at=item.delivered_at,
        status=customer_request_status(item.status),
        milestones=build_milestones(item),
        can_cancel=item.status in CANCELABLE_STATUSES,
        next_action=_next_action(item),
        href=href,
        receipt_href=href,
        artifact=customer_artifact,
        stage=_stage(item, copy_pending=copy_pending, downloaded=downloaded, now=moment),
        can_request_copy=can_ask_for_copy(item, now=moment) and not copy_pending,
        cost=_cost(item),
        history=_history(item, copy_pending=copy_pending, now=moment),
        leads_by_state=leads_by_state,
    )


def _downloaded_ids(db: Session, items: list[LeadRequest], *, now: datetime) -> set[str]:
    """Which of these batches' files expiring soon were ever downloaded.

    Downloads belong to the Customer, not the User Account that made them, so
    any recorded download of the batch counts.
    """

    candidates = [
        str(item.id)
        for item in items
        if item.status == RequestStatus.delivered.value
        and item.artifact is not None
        and (expires_at := _as_utc(item.artifact.expires_at)) is not None
        and now < expires_at <= now + ARTIFACT_EXPIRING_SOON
    ]
    if not candidates:
        return set()
    return set(
        db.scalars(
            select(AuditEntry.target_id).where(
                AuditEntry.action == "batch_artifact_downloaded",
                AuditEntry.target_type == "batch_request",
                AuditEntry.target_id.in_(candidates),
            )
        )
    )


def leads_by_state(db: Session, item: LeadRequest) -> list[CustomerBatchStateCount] | None:
    """A delivered batch's leads per state, from what was delivered."""

    if item.status != RequestStatus.delivered.value:
        return None
    rows = db.execute(
        select(DistributionEvent.state, func.count(DistributionEvent.id))
        .where(DistributionEvent.request_id == item.id)
        .group_by(DistributionEvent.state)
        .order_by(DistributionEvent.state)
    ).all()
    return [
        CustomerBatchStateCount(
            state=state or "",
            name=_STATE_NAMES.get(state, state) if state else "State not recorded",
            lead_count=int(count),
        )
        for state, count in rows
    ]


def customer_batches(
    db: Session,
    items: list[LeadRequest],
    *,
    now: datetime | None = None,
) -> list[CustomerRequestDetail]:
    """Several batches as the Customer reads them, in two extra queries."""

    moment = now or datetime.now(timezone.utc)
    pending = pending_copy_ids(db, [item.id for item in items])
    downloaded = _downloaded_ids(db, items, now=moment)
    return [
        request_detail(
            item,
            copy_pending=item.id in pending,
            downloaded=str(item.id) in downloaded,
            now=moment,
        )
        for item in items
    ]


def customer_batch(
    db: Session,
    item: LeadRequest,
    *,
    with_states: bool = False,
    now: datetime | None = None,
) -> CustomerRequestDetail:
    """One batch as the Customer reads it; its page adds leads per state."""

    moment = now or datetime.now(timezone.utc)
    detail = customer_batches(db, [item], now=moment)[0]
    if with_states:
        detail.leads_by_state = leads_by_state(db, item)
    return detail


def find_customer_batch(
    db: Session,
    reference: str,
    scope: ColumnElement[bool],
    *,
    for_update: bool = False,
) -> LeadRequest | None:
    """One of the Customer's batches by number ("1042") or by UUID.

    Older links (emails, bookmarks) carry the UUID; the portal names a batch
    by its number. Anything outside ``scope`` reads as not found.
    """

    text = reference.strip()
    if text.isdigit():
        if len(text) > 9:
            return None
        condition = LeadRequest.number == int(text)
    else:
        try:
            condition = LeadRequest.id == uuid.UUID(text)
        except ValueError:
            return None
    statement = select(LeadRequest).where(condition, scope)
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def build_request_workspace(
    db: Session,
    *,
    user_id: uuid.UUID,
    profile: CustomerProfile,
    page: int = 1,
    page_size: int = REQUEST_HISTORY_PAGE_SIZE,
) -> CustomerRequestWorkspaceOut:
    """Everything the guided Batch Request screen renders in one read.

    History is one page of the Customer's requests, newest first. A page past
    the end answers with the last page rather than an empty one.
    """

    scope = request_history_scope(profile, user_id)
    total = int(
        db.scalar(select(func.count()).select_from(LeadRequest).where(scope))
        or 0
    )
    page = min(max(1, page), max(1, ceil(total / page_size)))
    items = list(
        db.scalars(
            select(LeadRequest)
            .where(scope)
            .options(
                selectinload(LeadRequest.artifact),
                selectinload(LeadRequest.billing_hold),
            )
            .order_by(LeadRequest.created_at.desc(), LeadRequest.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return CustomerRequestWorkspaceOut(
        limits=request_limits(profile),
        blocker=request_blocker(profile),
        requests=customer_batches(db, items),
        history=CustomerRequestHistoryPage(
            page=page,
            page_size=page_size,
            total=total,
        ),
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
            "These are not appointed states on your account: "
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
            expected_lead_rate_cents_per_thousand=payload.expected_lead_rate_cents_per_thousand,
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

    profile = db.get(CustomerProfile, user_id)
    item = db.scalar(
        select(LeadRequest)
        .where(
            LeadRequest.id == request_id,
            request_history_scope(profile, user_id) if profile is not None else LeadRequest.user_id == user_id,
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
