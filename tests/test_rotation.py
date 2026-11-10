"""Fulfillment Rotation order (CONTEXT.md, "Fulfillment Rotation").

Seams: ``rotation_turns`` and ``rotation_order`` as pure functions over
``RotationCandidate`` values, and the parity of ``rotation_order`` with the
order ``fulfill_round_robin`` actually fulfills requests in.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from jawnix import allocation
from jawnix.allocation import fulfill_round_robin
from jawnix.batches import describe_batch
from jawnix.models import Agency, Agent, Lead, LeadRequest, RequestStatus
from jawnix.rotation import (
    RotationCandidate,
    in_rotation,
    rotation_candidate,
    rotation_order,
    rotation_turns,
)

from conftest import make_request

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ago(**delta) -> datetime:
    return NOW - timedelta(**delta)


def candidate(
    name: str,
    customer_id: int,
    *,
    agency_id: int | None = None,
    queued_at: datetime = ago(hours=1),
    customer_fulfilled_at: datetime | None = None,
    agency_fulfilled_at: datetime | None = None,
) -> RotationCandidate:
    return RotationCandidate(
        request_id=uuid.uuid5(uuid.NAMESPACE_URL, name),
        customer_id=customer_id,
        agency_id=agency_id,
        queued_at=queued_at,
        customer_fulfilled_at=customer_fulfilled_at,
        agency_fulfilled_at=agency_fulfilled_at,
    )


def names(order, candidates) -> list[str]:
    by_id = {value.request_id: key for key, value in candidates.items()}
    return [by_id[item.request_id] for item in order]


# --- One pass -----------------------------------------------------------------


def test_never_fulfilled_agencies_go_first_then_least_recent():
    candidates = {
        "recent": candidate("recent", 1, agency_id=10, agency_fulfilled_at=ago(hours=2)),
        "older": candidate("older", 2, agency_id=20, agency_fulfilled_at=ago(days=3)),
        "never": candidate("never", 3, agency_id=30),
    }
    assert names(rotation_turns(candidates.values()), candidates) == ["never", "older", "recent"]


def test_a_standalone_customer_is_a_one_customer_agency():
    candidates = {
        "agency": candidate("agency", 1, agency_id=10, agency_fulfilled_at=ago(days=2)),
        # Older than the agency's last fulfillment, so it goes first.
        "standalone": candidate("standalone", 2, customer_fulfilled_at=ago(days=5)),
        "recent standalone": candidate("recent standalone", 3, customer_fulfilled_at=ago(hours=1)),
    }
    assert names(rotation_turns(candidates.values()), candidates) == [
        "standalone",
        "agency",
        "recent standalone",
    ]


def test_an_agency_turn_takes_one_request_from_its_least_recent_customer():
    candidates = {
        "busy oldest": candidate(
            "busy oldest", 1, agency_id=10, queued_at=ago(days=9), customer_fulfilled_at=ago(hours=1)
        ),
        "quiet newer": candidate(
            "quiet newer", 2, agency_id=10, queued_at=ago(hours=2), customer_fulfilled_at=ago(days=4)
        ),
        "quiet older": candidate(
            "quiet older", 2, agency_id=10, queued_at=ago(hours=5), customer_fulfilled_at=ago(days=4)
        ),
    }
    # One turn for the agency: its least recently fulfilled Customer's oldest
    # request, even though another Customer's request is older still.
    assert names(rotation_turns(candidates.values()), candidates) == ["quiet older"]


def test_ties_fall_to_the_lower_id_whatever_the_input_order():
    tie = ago(hours=3)
    candidates = {f"r{index}": candidate(f"r{index}", 1, queued_at=tie) for index in range(5)}
    lowest = min(candidates.values(), key=lambda item: item.request_id)
    for seed in range(5):
        shuffled = list(candidates.values())
        random.Random(seed).shuffle(shuffled)
        assert rotation_turns(shuffled) == [lowest]


def test_never_fulfilled_ties_put_agencies_before_standalone_customers():
    candidates = {
        "standalone": candidate("standalone", 1),
        "agency": candidate("agency", 2, agency_id=99),
    }
    assert names(rotation_turns(candidates.values()), candidates) == ["agency", "standalone"]


# --- Every turn ---------------------------------------------------------------


def test_the_order_takes_one_request_per_agency_per_pass():
    candidates = {
        "a1": candidate("a1", 1, agency_id=10, queued_at=ago(days=3)),
        "a2": candidate("a2", 1, agency_id=10, queued_at=ago(days=2)),
        "a3": candidate("a3", 1, agency_id=10, queued_at=ago(days=1)),
        "b1": candidate("b1", 2, agency_fulfilled_at=None, customer_fulfilled_at=ago(days=1)),
    }
    assert names(rotation_order(list(candidates.values()), now=NOW), candidates) == [
        "a1",
        "b1",
        "a2",
        "a3",
    ]


def test_a_fulfilled_customer_waits_behind_its_agency_siblings():
    candidates = {
        "first customer": candidate("first customer", 1, agency_id=10, queued_at=ago(days=5)),
        "first customer again": candidate("first customer again", 1, agency_id=10, queued_at=ago(days=4)),
        "second customer": candidate("second customer", 2, agency_id=10, queued_at=ago(days=1)),
    }
    # Both Customers were never fulfilled, so the lower id goes first; after
    # its turn it is the agency's most recently fulfilled Customer.
    assert names(rotation_order(list(candidates.values()), now=NOW), candidates) == [
        "first customer",
        "second customer",
        "first customer again",
    ]


def test_the_order_holds_every_candidate_once():
    rng = random.Random(7)
    candidates = {
        f"r{index}": candidate(
            f"r{index}",
            rng.randint(1, 6),
            agency_id=rng.choice([None, 10, 20]),
            queued_at=ago(hours=rng.randint(1, 200)),
            customer_fulfilled_at=rng.choice([None, ago(days=rng.randint(1, 30))]),
            agency_fulfilled_at=rng.choice([None, ago(days=rng.randint(1, 30))]),
        )
        for index in range(30)
    }
    order = rotation_order(list(candidates.values()), now=NOW)
    assert sorted(item.request_id for item in order) == sorted(
        item.request_id for item in candidates.values()
    )
    assert rotation_order([], now=NOW) == []


def test_future_fulfillment_times_still_sort_behind_a_simulated_turn():
    # A clock skewed ahead must not let a just-simulated turn jump the queue.
    candidates = {
        "skewed": candidate("skewed", 1, customer_fulfilled_at=NOW + timedelta(hours=1)),
        "fresh": candidate("fresh", 2, queued_at=ago(hours=2)),
        "fresh again": candidate("fresh again", 2, queued_at=ago(hours=1)),
    }
    assert names(rotation_order(list(candidates.values()), now=NOW), candidates) == [
        "fresh",
        "skewed",
        "fresh again",
    ]


# --- The worker takes the same turns -----------------------------------------


def build_rotation_scene(session) -> dict[str, LeadRequest]:
    """Two agencies, two standalone customers, and one deactivated one."""
    early = Agency(slug="early", name="Early Agency", last_fulfilled_at=ago(days=3))
    fresh = Agency(slug="fresh", name="Fresh Agency")
    session.add_all([early, fresh])
    session.flush()
    early_regular = Agent(slug="early-regular", name="Early Regular", agency=early, last_fulfilled_at=ago(days=3))
    early_new = Agent(slug="early-new", name="Early New", agency=early)
    fresh_only = Agent(slug="fresh-only", name="Fresh Only", agency=fresh)
    lately = Agent(slug="lately", name="Lately", last_fulfilled_at=ago(days=1))
    waiting = Agent(slug="waiting", name="Waiting Standalone")
    gone = Agent(slug="gone", name="Gone", active=False)
    session.add_all([early_regular, early_new, fresh_only, lately, waiting, gone])
    session.flush()

    def request(owner, label, *, hours_ago, status="approved", approved_hours_ago=None, state="TX"):
        item = make_request(session, owner, 1, [state])
        item.status = status
        item.created_at = ago(hours=hours_ago)
        item.approved_at = ago(hours=approved_hours_ago) if approved_hours_ago is not None else None
        item.status_message = label
        return item

    requests = {
        "early regular 1": request(early_regular, "early regular 1", hours_ago=50),
        "early regular 2": request(early_regular, "early regular 2", hours_ago=40),
        "early new": request(early_new, "early new", hours_ago=10),
        # Submitted first but approved later: approval is when it joined.
        "fresh late approval": request(fresh_only, "fresh late approval", hours_ago=30, approved_hours_ago=5),
        "fresh early approval": request(fresh_only, "fresh early approval", hours_ago=20, approved_hours_ago=19),
        "lately": request(lately, "lately", hours_ago=60),
        # Its own state, so no newer request needs an Inventory Conflict
        # decision to pass it: that would hold a turn, not reorder them.
        "waiting": request(waiting, "waiting", hours_ago=70, status="waiting_inventory", state="FL"),
        "gone": request(gone, "gone", hours_ago=80),
        "pending": request(lately, "pending", hours_ago=1, status="pending"),
    }
    session.add_all(
        Lead(phone=f"21455570{index:02d}", title=f"Rotation {index}", state="TX")
        for index in range(12)
    )
    session.add(Lead(phone="3055557000", title="Rotation FL", state="FL"))
    session.commit()
    return requests


EXPECTED = [
    "fresh early approval",
    "waiting",
    "early new",
    "lately",
    "fresh late approval",
    "early regular 1",
    "early regular 2",
]


def assert_batch_page_matches_the_worker(session, settings, monkeypatch) -> None:
    """Every Batch page's place in rotation is the turn the worker takes it on."""
    requests = build_rotation_scene(session)
    labels = {item.id: label for label, item in requests.items()}

    places = {
        label: describe_batch(session, item)["fill"]["rotation"]
        for label, item in requests.items()
    }
    assert {label: places[label]["aheadCount"] for label in EXPECTED} == {
        label: index for index, label in enumerate(EXPECTED)
    }
    assert [item["number"] for item in places["early regular 2"]["ahead"]] == [
        requests[label].number for label in EXPECTED[:5]
    ]
    assert places["gone"]["state"] == "paused"
    # Approved now, the pending batch would take Lately's second turn: after
    # both agencies' second turns, before Early Agency's third.
    assert places["pending"]["state"] == "pending_approval"
    assert places["pending"]["aheadCount"] == EXPECTED.index("early regular 1") + 1

    fulfilled: list[str] = []
    real_allocate = allocation.allocate_request

    def recording_allocate(db, request_id, settings_):
        fulfilled.append(labels[request_id])
        return real_allocate(db, request_id, settings_)

    monkeypatch.setattr(allocation, "allocate_request", recording_allocate)
    for _ in range(10):
        if fulfill_round_robin(session, settings)["requestsFulfilled"] == 0:
            break
        session.commit()

    assert fulfilled == EXPECTED
    assert requests["gone"].status == RequestStatus.approved.value
    assert requests["pending"].status == RequestStatus.pending.value
    assert all(
        session.get(LeadRequest, requests[label].id).status == RequestStatus.generated.value
        for label in EXPECTED
    )


def test_the_projection_is_the_order_the_worker_fulfills(session, settings):
    requests = build_rotation_scene(session)
    queued = [
        item
        for item in requests.values()
        if item.status in {"approved", "waiting_inventory"} and in_rotation(item.customer)
    ]
    labels = {item.id: label for label, item in requests.items()}
    assert [
        labels[turn.request_id]
        for turn in rotation_order([rotation_candidate(item) for item in queued], now=NOW)
    ] == EXPECTED


def test_the_batch_page_place_is_the_turn_the_worker_takes(session, settings, monkeypatch):
    assert_batch_page_matches_the_worker(session, settings, monkeypatch)
