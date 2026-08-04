from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from jawnix.allocation import allocate_request, fulfill_round_robin
from jawnix.metrics_emit import EMIT_LEAD_ASSIGNED_JOB
from jawnix.models import (
    Agency,
    Agent,
    BatchArtifact,
    DistributionEvent,
    InventoryConflict,
    Job,
    Lead,
    RequestStatus,
)

from conftest import make_request


def test_exact_allocation_and_csv(session, settings):
    agent = Agent(slug="alice", name="Alice")
    session.add(agent)
    session.flush()
    session.add_all(
        [
            Lead(phone="2155550001", title='Owner, "Acme"', state="PA"),
            Lead(phone="2155550002", title="Manager", state="PA"),
            Lead(phone="2125550003", title="Wrong state", state="NY"),
        ]
    )
    request = make_request(session, agent, 2, ["PA"])

    result = allocate_request(session, request.id, settings)
    session.commit()

    assert result.allocated == 2
    assert request.status == RequestStatus.generated.value
    artifact = session.scalar(select(BatchArtifact).where(BatchArtifact.request_id == request.id))
    assert artifact is not None and artifact.row_count == 2 and len(artifact.sha256) == 64
    assert artifact.parts == [
        {"filename": artifact.parts[0]["filename"], "row_count": 2}
    ]
    with zipfile.ZipFile(artifact.path) as archive:
        rows = list(
            csv.DictReader(
                io.StringIO(
                    archive.read(artifact.parts[0]["filename"]).decode()
                )
            )
        )
    assert list(rows[0]) == ["phone", "title"]
    assert len(rows) == 2
    assert len({row["phone"] for row in rows}) == 2
    assert {row["title"] for row in rows} == {'Owner, "Acme"', "Manager"}


def test_shortage_allocates_nothing(session, settings):
    agent = Agent(slug="short", name="Short")
    session.add(agent)
    session.flush()
    session.add(Lead(phone="2145550001", title="One", state="TX"))
    request = make_request(session, agent, 2)

    result = allocate_request(session, request.id, settings)
    session.commit()

    assert result.status == RequestStatus.waiting_inventory.value
    assert result.allocated == 0
    assert request.available_count == 1
    assert session.scalar(select(func.count(DistributionEvent.id))) == 0
    assert session.scalar(select(func.count(BatchArtifact.id))) == 0


def test_agency_history_is_permanent_and_global_cooldown_applies(session, settings):
    agency = Agency(slug="shared", name="Shared")
    first = Agent(slug="first", name="First", agency=agency)
    second = Agent(slug="second", name="Second", agency=agency)
    outsider = Agent(slug="outside", name="Outside")
    session.add_all([agency, first, second, outsider])
    session.flush()
    old = datetime.now(timezone.utc) - timedelta(days=30)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    same_agency = Lead(phone="2145551001", title="Same agency", state="TX", last_distributed_at=old)
    cooling = Lead(phone="2145551002", title="Cooling", state="TX", last_distributed_at=recent)
    eligible = Lead(phone="2145551003", title="Eligible", state="TX", last_distributed_at=old)
    session.add_all([same_agency, cooling, eligible])
    session.flush()
    session.add_all(
        [
            DistributionEvent(lead_id=same_agency.id, agent_id=first.id, delivered_at=old, source="test"),
            DistributionEvent(lead_id=cooling.id, agent_id=outsider.id, delivered_at=recent, source="test"),
            DistributionEvent(lead_id=eligible.id, agent_id=outsider.id, delivered_at=old, source="test"),
        ]
    )
    request = make_request(session, second, 1)

    result = allocate_request(session, request.id, settings)
    session.commit()

    assert result.allocated == 1
    event = session.scalar(select(DistributionEvent).where(DistributionEvent.request_id == request.id))
    assert event.lead_id == eligible.id


def test_retry_reuses_existing_allocation(session, settings):
    agent = Agent(slug="reuse", name="Reuse")
    session.add(agent)
    session.flush()
    session.add(Lead(phone="2145552001", title="One", state="TX"))
    request = make_request(session, agent, 1)
    first = allocate_request(session, request.id, settings)
    session.flush()
    event_id = session.scalar(select(DistributionEvent.id).where(DistributionEvent.request_id == request.id))
    request.status = RequestStatus.approved.value
    second = allocate_request(session, request.id, settings)
    session.commit()

    assert first.allocated == second.allocated == 1
    assert session.scalar(select(func.count(DistributionEvent.id))) == 1
    assert session.scalar(select(DistributionEvent.id).where(DistributionEvent.request_id == request.id)) == event_id


def test_allocate_enqueues_emit_lead_assigned_on_fresh_and_replay(session, settings):
    agent = Agent(slug="emit-queue", name="Emit Queue")
    session.add(agent)
    session.flush()
    session.add(Lead(phone="2145552101", title="One", state="TX"))
    request = make_request(session, agent, 1)

    allocate_request(session, request.id, settings)
    session.flush()
    fresh_jobs = list(
        session.scalars(
            select(Job).where(
                Job.kind == EMIT_LEAD_ASSIGNED_JOB,
                Job.request_id == request.id,
            )
        )
    )
    assert len(fresh_jobs) == 1

    request.status = RequestStatus.approved.value
    allocate_request(session, request.id, settings)
    session.commit()
    replay_jobs = list(
        session.scalars(
            select(Job).where(
                Job.kind == EMIT_LEAD_ASSIGNED_JOB,
                Job.request_id == request.id,
            )
        )
    )
    # Replay re-enqueues; metrics dedups on distribution_event.id.
    assert len(replay_jobs) == 2
    deliver_jobs = list(
        session.scalars(
            select(Job).where(
                Job.kind == "deliver_request",
                Job.request_id == request.id,
            )
        )
    )
    assert len(deliver_jobs) == 2


def test_allocation_snapshots_customer_agency_and_delivered_listing(session, settings):
    original_agency = Agency(slug="original-agency", name="Original Agency")
    moved_agency = Agency(slug="moved-agency", name="Moved Agency")
    customer = Agent(
        slug="snapshot-customer",
        name="Snapshot Customer",
        agency=original_agency,
    )
    lead = Lead(
        phone="2145553001",
        title="Original Listing",
        state="TX",
        source_flow="google_maps",
    )
    session.add_all([original_agency, moved_agency, customer, lead])
    session.flush()
    request = make_request(session, customer, 1)

    allocate_request(session, request.id, settings)
    session.commit()
    event = session.scalar(
        select(DistributionEvent).where(DistributionEvent.request_id == request.id)
    )
    assert event.customer_id == customer.id
    assert event.customer_name == "Snapshot Customer"
    assert event.agency_id == original_agency.id
    assert event.agency_name == "Original Agency"
    assert event.phone == "2145553001"
    assert event.title == "Original Listing"
    assert event.state == "TX"
    assert event.listing_provenance == {"kind": "legacy", "source": "google_maps"}

    customer.name = "Renamed Customer"
    customer.agency = moved_agency
    lead.title = "Changed Listing"
    lead.state = "FL"
    artifact = session.scalar(
        select(BatchArtifact).where(BatchArtifact.request_id == request.id)
    )
    assert artifact is not None
    artifact_path = artifact.path
    session.commit()

    # Regeneration and delivery retries use the immutable event values, not
    # mutable Customer membership or the Lead's current listing.
    import os

    os.unlink(artifact_path)
    request.status = RequestStatus.approved.value
    allocate_request(session, request.id, settings)
    session.commit()
    with zipfile.ZipFile(artifact.path) as archive:
        stream = io.StringIO(
            archive.read(artifact.parts[0]["filename"]).decode()
        )
        assert list(csv.DictReader(stream)) == [
            {"phone": "2145553001", "title": "Original Listing"}
        ]

    session.refresh(event)
    assert event.customer_name == "Snapshot Customer"
    assert event.agency_id == original_agency.id
    assert event.agency_name == "Original Agency"


def test_agency_first_round_robin_fulfills_at_most_one_request_per_agency_turn(
    session,
    settings,
):
    shared = Agency(slug="round-robin", name="Round Robin")
    first = Agent(slug="first-customer", name="First Customer", agency=shared)
    second = Agent(slug="second-customer", name="Second Customer", agency=shared)
    standalone = Agent(slug="standalone-customer", name="Standalone Customer")
    session.add_all([shared, first, second, standalone])
    session.flush()
    first_request = make_request(session, first, 1)
    second_request = make_request(session, second, 1)
    standalone_request = make_request(session, standalone, 1)
    session.add_all(
        [
            Lead(phone="2145554001", title="One", state="TX"),
            Lead(phone="2145554002", title="Two", state="TX"),
            Lead(phone="2145554003", title="Three", state="TX"),
        ]
    )

    first_round = fulfill_round_robin(session, settings)
    session.commit()

    assert first_round == {
        "agenciesVisited": 2,
        "requestsFulfilled": 2,
        "requestsWaiting": 0,
    }
    assert first_request.status == RequestStatus.generated.value
    assert standalone_request.status == RequestStatus.generated.value
    assert second_request.status == RequestStatus.approved.value
    assert shared.last_fulfilled_at is not None
    assert first.last_fulfilled_at is not None
    assert second.last_fulfilled_at is None
    assert standalone.last_fulfilled_at is not None

    second_round = fulfill_round_robin(session, settings)
    session.commit()
    assert second_round["requestsFulfilled"] == 1
    assert second_request.status == RequestStatus.generated.value
    assert second.last_fulfilled_at is not None


def test_newer_request_needs_one_scope_bound_conflict_decision_to_bypass_older(
    session,
    settings,
):
    older_customer = Agent(slug="older-customer", name="Older Customer")
    newer_customer = Agent(slug="newer-customer", name="Newer Customer")
    unrelated = Agent(slug="unrelated-customer", name="Unrelated Customer")
    session.add_all([older_customer, newer_customer, unrelated])
    session.flush()
    older = make_request(session, older_customer, 2, ["TX"])
    older.status = RequestStatus.waiting_inventory.value
    older.available_count = 1
    newer = make_request(session, newer_customer, 1, ["TX"])
    newer.created_at = older.created_at + timedelta(seconds=1)
    other = make_request(session, unrelated, 1, ["FL"])
    session.add_all(
        [
            Lead(phone="2145554401", title="Shared", state="TX"),
            Lead(phone="3055554402", title="Unrelated", state="FL"),
        ]
    )
    session.commit()

    first = fulfill_round_robin(session, settings)
    session.commit()

    assert first["requestsFulfilled"] == 1
    assert other.status == RequestStatus.generated.value
    assert newer.status == RequestStatus.waiting_inventory.value
    conflict = session.query(InventoryConflict).one()
    assert conflict.older_request_id == older.id
    assert conflict.newer_request_id == newer.id
    assert conflict.status == "pending"

    conflict.status = "confirmed"
    session.commit()
    second = fulfill_round_robin(session, settings)
    session.commit()

    assert second["requestsFulfilled"] == 1
    assert newer.status == RequestStatus.generated.value
    session.refresh(conflict)
    assert conflict.status == "consumed"
    assert session.query(InventoryConflict).count() == 1
