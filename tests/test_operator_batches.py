"""All batches and the Batch page.

Seam: seeded Batch Requests in; ``GET /api/admin/batches`` and
``GET /api/admin/batches/{number or id}`` out.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.batches import list_batches
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    Agent,
    BatchArtifact,
    BatchHold,
    CreditLedgerEntry,
    CustomerAvailabilitySnapshot,
    CustomerProfile,
    DistributionEvent,
    InventoryConflict,
    Lead,
    LeadReport,
    LeadRequest,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def customer(session, slug: str, **kwargs) -> Agent:
    item = Agent(slug=slug, name=slug.replace("-", " ").title(), **kwargs)
    session.add(item)
    session.flush()
    return item


def batch(
    session,
    owner: Agent,
    status: str,
    *,
    lead_count: int = 100,
    hours_ago: float = 1,
    **kwargs,
) -> LeadRequest:
    user_id = uuid.uuid4()
    session.add(
        CustomerProfile(
            user_id=user_id,
            email=f"{user_id}@example.com",
            first_name="River",
            last_name="Morgan",
            licensed_states=["FL"],
            agent=owner,
        )
    )
    session.flush()
    item = LeadRequest(
        user_id=user_id,
        agent=owner,
        lead_count=lead_count,
        states_snapshot=["FL"],
        delivery_email="river@example.com",
        status=status,
        created_at=NOW - timedelta(hours=hours_ago),
        **kwargs,
    )
    session.add(item)
    session.flush()
    return item


def artifact(session, item: LeadRequest, *, expires_in_days: float = 20, **kwargs) -> BatchArtifact:
    value = BatchArtifact(
        request_id=item.id,
        path="/nonexistent/batch.csv",
        filename="batch.csv",
        row_count=item.lead_count,
        parts=[],
        byte_count=10,
        sha256="a" * 64,
        expires_at=NOW + timedelta(days=expires_in_days),
        **kwargs,
    )
    session.add(value)
    session.flush()
    return value


def distribute(session, item: LeadRequest, database: str, count: int = 1) -> None:
    for index in range(count):
        lead = Lead(phone=f"215555{uuid.uuid4().int % 10_000:04d}", title="Dental", state="FL")
        session.add(lead)
        session.flush()
        session.add(
            DistributionEvent(
                lead_id=lead.id,
                agent_id=item.agent_id,
                request_id=item.id,
                inventory_database=database,
            )
        )
    session.flush()


def as_admin(session) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    return TestClient(app)


@pytest.fixture
def client(session):
    yield as_admin(session)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(session):
    northstar = customer(session, "northstar-insurance")
    ridgeline = customer(session, "ridgeline-benefits")
    keystone = customer(session, "keystone-medicare", lead_database="db2")
    delivered = batch(
        session, northstar, "delivered", lead_count=50_000, hours_ago=72,
        delivered_at=NOW - timedelta(hours=70),
    )
    artifact(session, delivered)
    expired = batch(
        session, keystone, "delivered", hours_ago=24 * 40,
        delivered_at=NOW - timedelta(days=39),
    )
    artifact(session, expired, expires_in_days=-9)
    distribute(session, expired, "db2")
    waiting = batch(
        session, ridgeline, "waiting_inventory", lead_count=100_000,
        hours_ago=60, available_count=68_410,
    )
    failed = batch(session, northstar, "failed", hours_ago=3)
    artifact(session, failed, delivery_status="failed", delivery_attempts=2)
    pending = batch(session, northstar, "pending", lead_count=25_000, hours_ago=2)
    canceled = batch(session, keystone, "canceled", hours_ago=5)
    session.add(
        CustomerAvailabilitySnapshot(
            customer_id=northstar.id, available=1_188_205, computed_at=NOW - timedelta(hours=6)
        )
    )
    session.commit()
    return {
        "northstar": northstar,
        "ridgeline": ridgeline,
        "keystone": keystone,
        "delivered": delivered,
        "expired": expired,
        "waiting": waiting,
        "failed": failed,
        "pending": pending,
        "canceled": canceled,
    }


def numbers(result) -> list[int]:
    return [row["number"] for row in result["items"]]


def test_open_batches_come_oldest_first_with_their_stage_and_fill(session, seeded):
    result = list_batches(session)
    assert numbers(result) == [
        seeded["waiting"].number,
        seeded["failed"].number,
        seeded["pending"].number,
    ]
    rows = {row["number"]: row for row in result["items"]}
    waiting = rows[seeded["waiting"].number]
    assert waiting["stage"] == "waiting"
    assert waiting["availability"] == {
        "kind": "short",
        "available": 68_410,
        "short": 31_590,
        "asOf": None,
        "source": "last_attempt",
    }
    failed = rows[seeded["failed"].number]
    assert failed["stage"] == "delivery_failed"
    assert failed["needsDecision"] is True
    assert failed["availability"] == {"kind": "committed", "committed": 100}
    pending = rows[seeded["pending"].number]
    assert pending["stage"] == "awaiting_approval"
    assert pending["availability"]["kind"] == "fillable"
    assert pending["availability"]["available"] == 1_188_205
    assert pending["availability"]["source"] == "customer_snapshot"
    assert pending["href"] == f"/app/admin/batches/{seeded['pending'].number}"
    assert result["total"] == 3
    assert result["counts"] == {"delivered": 2, "expired": 1}


def test_status_filters_include_expired_files_and_closed_batches(session, seeded):
    delivered = list_batches(session, status="delivered")
    # Settled batches read newest first.
    assert numbers(delivered) == [seeded["delivered"].number, seeded["expired"].number]
    expired = list_batches(session, status="expired")
    assert numbers(expired) == [seeded["expired"].number]
    assert expired["items"][0]["stage"] == "file_expired"
    assert numbers(list_batches(session, status="closed")) == [seeded["canceled"].number]
    assert list_batches(session, status="all")["total"] == 6


def test_database_filter_reads_what_a_batch_drew_from(session, seeded):
    # Keystone now draws from DB2; its delivered batch drew from DB2 too.
    db2 = list_batches(session, status="all", database="db2")
    assert numbers(db2) == [seeded["canceled"].number, seeded["expired"].number]
    assert {row["database"] for row in db2["items"]} == {"db2"}
    db1 = list_batches(session, status="all", database="db1")
    assert seeded["expired"].number not in numbers(db1)
    assert db1["total"] == 4


def test_needs_a_decision_covers_approvals_failures_and_conflicts(session, seeded):
    assert set(numbers(list_batches(session, needs_decision=True))) == {
        seeded["failed"].number,
        seeded["pending"].number,
    }
    session.add(
        InventoryConflict(
            older_request_id=seeded["waiting"].id,
            newer_request_id=seeded["delivered"].id,
            inventory_snapshot={},
            snapshot_checksum="c" * 64,
            status="pending",
        )
    )
    session.commit()
    result = list_batches(session, status="all", needs_decision=True)
    assert seeded["waiting"].number in numbers(result)
    assert seeded["delivered"].number in numbers(result)


@pytest.mark.parametrize(
    "query",
    ["{number}", "#{number}", "Batch #{number}", "REQ-{prefix}", "{uuid}"],
)
def test_search_finds_a_batch_by_number_or_uuid(session, seeded, query):
    target = seeded["delivered"]
    text = query.format(
        number=target.number,
        prefix=target.id.hex[:8].upper(),
        uuid=str(target.id),
    )
    assert numbers(list_batches(session, status="all", q=text)) == [target.number]


def test_search_matches_customer_names_and_escapes_wildcards(session, seeded):
    assert numbers(list_batches(session, status="all", q="ridgeline")) == [
        seeded["waiting"].number
    ]
    assert list_batches(session, status="all", q="%")["total"] == 0


def test_paging_reports_its_position(session, seeded):
    first = list_batches(session, status="all", page_size=4)
    second = list_batches(session, status="all", page=2, page_size=4)
    assert (first["page"], first["pages"], first["total"]) == (1, 2, 6)
    assert len(first["items"]) == 4
    assert len(second["items"]) == 2
    assert not set(numbers(first)) & set(numbers(second))
    # A page past the end returns the last page instead of nothing.
    assert list_batches(session, status="all", page=9, page_size=4)["page"] == 2


def test_batches_endpoint_reads_filters_in_camel_case(session, seeded, client):
    body = client.get(
        "/api/admin/batches", params={"status": "all", "needsDecision": "true", "pageSize": 10}
    ).json()
    assert body["pageSize"] == 10
    assert {row["number"] for row in body["items"]} == {
        seeded["failed"].number,
        seeded["pending"].number,
    }


def test_batch_page_by_number_and_by_uuid(session, seeded, client):
    agency = Agency(slug="lakeside", name="Lakeside Agency")
    session.add(agency)
    session.flush()
    northstar = seeded["northstar"]
    northstar.agency_id = agency.id
    # Northstar's earlier deliveries were its agency's last turn.
    agency.last_fulfilled_at = NOW - timedelta(hours=70)
    northstar.last_fulfilled_at = NOW - timedelta(hours=70)
    northstar.billing_enabled = True
    northstar.lead_rate_cents_per_thousand = 500
    pending = seeded["pending"]
    pending.is_billed = True
    pending.lead_rate_cents_per_thousand = 500
    pending.billing_amount_cents = 12_500
    session.add_all(
        [
            CreditLedgerEntry(
                customer_id=northstar.id,
                kind="purchase",
                amount_cents=53_750,
                reason="Fixture",
                actor_user_id="system:test",
            ),
            BatchHold(request_id=pending.id, customer_id=northstar.id, amount_cents=12_500),
        ]
    )
    session.commit()

    by_number = client.get(f"/api/admin/batches/{pending.number}")
    assert by_number.status_code == 200
    body = by_number.json()
    assert client.get(f"/api/admin/batches/{pending.id}").json()["number"] == pending.number
    assert body["number"] == pending.number
    assert body["stage"] == "awaiting_approval"
    assert body["needsDecision"] is True
    assert body["database"] == "db1"
    assert [action["name"] for action in body["actions"]] == ["approve", "reject", "cancel"]
    assert body["customerRecord"]["href"] == f"/app/admin/customers/{northstar.id}"
    assert body["agencyRecord"]["name"] == "Lakeside Agency"
    assert body["fill"]["kind"] == "fillable"
    assert body["fill"]["needed"] == 25_000
    assert body["fill"]["available"] == 1_188_205
    # Awaiting approval, so not in rotation yet. Approved now, it would wait
    # behind Ridgeline, which has never had a turn.
    assert body["fill"]["rotation"] == {
        "state": "pending_approval",
        "aheadCount": 1,
        "ahead": [
            {
                "number": seeded["waiting"].number,
                "href": f"/app/admin/batches/{seeded['waiting'].number}",
                "customer": "Ridgeline Benefits",
                "stage": "waiting",
            }
        ],
    }
    assert body["cost"] == {
        "billed": True,
        "rateCentsPerThousand": 500,
        "amountCents": 12_500,
        "hold": "active",
        "availableBalanceCents": 41_250,
    }
    assert [(step["label"], step["done"]) for step in body["progress"]] == [
        ("Submitted", True),
        ("Approved", False),
        ("Allocated", False),
        ("File generated", False),
        ("Delivered", False),
    ]
    assert [item["number"] for item in body["recentBatches"]] == [
        seeded["failed"].number,
        seeded["delivered"].number,
    ]
    assert body["openLeadReports"] == 0

    assert client.get("/api/admin/batches/999999").status_code == 404
    assert client.get("/api/admin/batches/not-a-batch").status_code == 404


def test_batch_page_ends_progress_where_a_batch_stopped(session, seeded, client):
    body = client.get(f"/api/admin/batches/{seeded['canceled'].number}").json()
    assert [step["label"] for step in body["progress"]] == ["Submitted", "Canceled"]
    assert body["fill"]["kind"] == "none"
    assert body["fill"]["rotation"] is None
    assert body["cost"] == {"billed": False}
    failed = client.get(f"/api/admin/batches/{seeded['failed'].number}").json()
    assert failed["progress"][-1]["label"] == "Delivery failed"
    assert failed["stage"] == "delivery_failed"


def rotation_of(client, item: LeadRequest) -> dict:
    return client.get(f"/api/admin/batches/{item.number}").json()["fill"]["rotation"]


def test_batch_page_places_approved_batches_in_rotation_order(session, seeded, client):
    keystone = seeded["keystone"]
    keystone.last_fulfilled_at = NOW - timedelta(days=1)
    approved = batch(session, keystone, "approved", hours_ago=90, approved_at=NOW - timedelta(hours=1))
    session.commit()

    # Ridgeline has never had a turn; Keystone had one yesterday, so its
    # batch waits behind Ridgeline's although it was submitted earlier.
    assert rotation_of(client, seeded["waiting"]) == {"state": "queued", "aheadCount": 0, "ahead": []}
    keystone_place = rotation_of(client, approved)
    assert keystone_place["state"] == "queued"
    assert keystone_place["aheadCount"] == 1
    assert [item["number"] for item in keystone_place["ahead"]] == [seeded["waiting"].number]
    # Generating, delivered, and closed batches have left the rotation.
    for done in ("delivered", "failed", "canceled"):
        assert rotation_of(client, seeded[done]) is None


def test_awaiting_approval_is_projected_behind_its_own_customers_turn(session, seeded, client):
    ridgeline = seeded["ridgeline"]
    pending = batch(session, ridgeline, "pending", hours_ago=0.5)
    session.commit()

    place = rotation_of(client, pending)
    # Its own Customer's waiting batch takes Ridgeline's next turn first.
    assert place["state"] == "pending_approval"
    assert place["aheadCount"] == 1
    assert place["ahead"][0]["number"] == seeded["waiting"].number


def test_batch_page_names_the_first_batches_ahead_and_counts_the_rest(session, seeded, client):
    keystone = seeded["keystone"]
    keystone.last_fulfilled_at = NOW - timedelta(hours=1)
    target = batch(session, keystone, "approved", hours_ago=200)
    for index in range(6):
        batch(session, customer(session, f"never-served-{index}"), "approved", hours_ago=10 + index)
    session.commit()

    place = rotation_of(client, target)
    assert place["aheadCount"] == 7
    assert len(place["ahead"]) == 5
    assert place["ahead"][0]["customer"] == "Ridgeline Benefits"


def test_a_deactivated_customer_or_agency_takes_no_turns(session, seeded, client):
    keystone = seeded["keystone"]
    approved = batch(session, keystone, "approved", hours_ago=4)
    keystone.active = False
    session.commit()
    assert rotation_of(client, approved) == {
        "state": "paused",
        "reason": "Not in rotation while the customer is deactivated",
        "aheadCount": 0,
        "ahead": [],
    }
    # Nor does it count ahead of anyone else.
    assert rotation_of(client, seeded["waiting"])["aheadCount"] == 0

    keystone.active = True
    keystone.agency = Agency(slug="closed-agency", name="Closed Agency", active=False)
    session.commit()
    assert rotation_of(client, approved)["reason"] == "Not in rotation while the agency is deactivated"


def test_batch_page_counts_the_customers_open_lead_reports(session, seeded, client):
    delivered = seeded["delivered"]
    distribute(session, delivered, "db1")
    event = session.query(DistributionEvent).filter_by(request_id=delivered.id).first()
    session.add(
        LeadReport(
            distribution_event_id=event.id,
            customer_id=delivered.agent_id,
            reason="invalid_phone",
        )
    )
    session.commit()
    body = client.get(f"/api/admin/batches/{seeded['pending'].number}").json()
    assert body["openLeadReports"] == 1


def test_request_contracts_carry_the_batch_number(session, seeded, client):
    detail = client.get(f"/api/admin/requests/{seeded['pending'].id}").json()
    assert detail["number"] == seeded["pending"].number
    fulfillment = client.get("/api/admin/fulfillment").json()
    assert {item["number"] for item in fulfillment["batchRequests"]} == {
        seeded["waiting"].number,
        seeded["failed"].number,
        seeded["pending"].number,
    }
