"""Fulfillment Rotation order, as one pure function shared by both callers.

``fulfill_round_robin`` takes each pass's turns from :func:`rotation_turns`,
and the Batch page projects a Batch's place from :func:`rotation_order`, which
only repeats those passes. One ordering, so what the page says is what the
worker does.

The rules (CONTEXT.md, "Fulfillment Rotation"): Agency-level round-robin by
least recent fulfillment, a standalone Customer counting as a one-Customer
Agency, never-fulfilled first. Each Agency turn takes its
least-recently-fulfilled Customer's oldest approved request, fulfills at most
one, and the rotation advances.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from .models import Customer, LeadRequest, RequestStatus

#: The statuses the rotation draws from. Pending requests have not entered it.
ROTATION_STATUSES = frozenset(
    {RequestStatus.approved.value, RequestStatus.waiting_inventory.value}
)

_NEVER = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RotationCandidate:
    """What the rotation knows about one approved or waiting request."""

    request_id: uuid.UUID
    customer_id: int
    agency_id: int | None
    #: When the request joined the rotation: approval, else submission.
    queued_at: datetime
    customer_fulfilled_at: datetime | None
    #: The Agency's last fulfillment; unused for a standalone Customer.
    agency_fulfilled_at: datetime | None = None

    @property
    def turn(self) -> tuple[str, int]:
        """Whose turn this request waits for: its Agency, else its Customer."""
        if self.agency_id is not None:
            return ("agency", self.agency_id)
        return ("customer", self.customer_id)

    @property
    def turn_fulfilled_at(self) -> datetime | None:
        if self.agency_id is not None:
            return self.agency_fulfilled_at
        return self.customer_fulfilled_at


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _least_recent_first(value: datetime | None) -> tuple[bool, datetime]:
    """Never-fulfilled first, then the oldest fulfillment."""
    return (value is not None, _utc(value) or _NEVER)


def in_rotation(customer: Customer) -> bool:
    """A deactivated or deleted Customer or Agency takes no turns."""
    if not customer.active or customer.deleted_at is not None:
        return False
    agency = customer.agency
    return agency is None or (agency.active and agency.deleted_at is None)


def rotation_candidate(
    request: LeadRequest,
    *,
    queued_at: datetime | None = None,
) -> RotationCandidate:
    """The rotation's view of a request. ``queued_at`` projects an approval."""
    customer = request.customer
    return RotationCandidate(
        request_id=request.id,
        customer_id=customer.id,
        agency_id=customer.agency_id,
        queued_at=_utc(queued_at or request.approved_at or request.created_at),
        customer_fulfilled_at=_utc(customer.last_fulfilled_at),
        agency_fulfilled_at=(
            _utc(customer.agency.last_fulfilled_at)
            if customer.agency is not None
            else None
        ),
    )


def rotation_turns(
    candidates: Iterable[RotationCandidate],
) -> list[RotationCandidate]:
    """One pass: each Agency's (or standalone Customer's) next request.

    Agencies go least recently fulfilled first; within an Agency, its least
    recently fulfilled Customer's oldest request goes. Ties fall to the lower
    id, so the order is total and never depends on input order.
    """
    heads: dict[tuple[str, int], RotationCandidate] = {}
    for candidate in candidates:
        key = (
            *_least_recent_first(candidate.customer_fulfilled_at),
            candidate.customer_id,
            candidate.queued_at,
            candidate.request_id,
        )
        current = heads.get(candidate.turn)
        if current is None or key < (
            *_least_recent_first(current.customer_fulfilled_at),
            current.customer_id,
            current.queued_at,
            current.request_id,
        ):
            heads[candidate.turn] = candidate
    return sorted(
        heads.values(),
        key=lambda item: (
            *_least_recent_first(item.turn_fulfilled_at),
            *item.turn,
        ),
    )


def rotation_order(
    candidates: Sequence[RotationCandidate],
    *,
    now: datetime,
) -> list[RotationCandidate]:
    """Every request in the order the rotation would take it.

    Repeats :func:`rotation_turns` the way the worker does, as though each
    turn fulfilled its request: that Customer and its Agency become the most
    recently fulfilled, and the next pass orders them behind everyone else.
    A turn that in practice waits for inventory, or for an Inventory
    Conflict decision, keeps its place instead, so this is the order of
    turns, not a delivery schedule.
    """
    remaining = list(candidates)
    order: list[RotationCandidate] = []
    clock = max(
        [now, *(
            value
            for item in remaining
            for value in (item.customer_fulfilled_at, item.agency_fulfilled_at)
            if value is not None
        )]
    )
    while remaining:
        for turn in rotation_turns(remaining):
            order.append(turn)
            clock += timedelta(microseconds=1)
            remaining = [
                replace(
                    item,
                    customer_fulfilled_at=(
                        clock
                        if item.customer_id == turn.customer_id
                        else item.customer_fulfilled_at
                    ),
                    agency_fulfilled_at=(
                        clock
                        if turn.agency_id is not None
                        and item.agency_id == turn.agency_id
                        else item.agency_fulfilled_at
                    ),
                )
                for item in remaining
                if item.request_id != turn.request_id
            ]
    return order
