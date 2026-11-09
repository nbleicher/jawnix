"""The Customers table and the Customer page (A3).

Seam: seeded Customers, Batch Requests, holds, reports, and conflicts in;
``GET /api/admin/customers/directory``, ``/summary``, ``/lead-reports``,
``/billing``, ``PUT /lead-database``, and ``GET /api/admin/batches?customer=``
out.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.customer_directory import build_customer_directory
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    Agent,
    AuditEntry,
    BatchArtifact,
    BatchHold,
    CreditLedgerEntry,
    CustomerAvailabilitySnapshot,
    CustomerProfile,
    DistributionEvent,
    InventoryConflict,
    Job,
    Lead,
    LeadReport,
    LeadRequest,
    UserAccount,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def customer(session, slug: str, *, states=("FL",), account=True, **kwargs) -> Agent:
    item = Agent(
        slug=slug,
        name=slug.replace("-", " ").title(),
        licensed_states=list(states),
        **kwargs,
    )
    session.add(item)
    session.flush()
    if account:
        session.add(
            UserAccount(
                auth_user_id=uuid.uuid4(),
                customer_id=item.id,
                email=f"owner@{slug}.example",
                created_at=NOW - timedelta(days=60),
            )
        )
        session.flush()
    return item


def batch(session, owner: Agent, status: str, *, lead_count=1_000, hours_ago=1.0, **kwargs) -> LeadRequest:
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


def artifact(session, item: LeadRequest, **kwargs) -> BatchArtifact:
    value = BatchArtifact(
        request_id=item.id,
        path="/nonexistent/batch.csv",
        filename="batch.csv",
        row_count=item.lead_count,
        parts=[],
        byte_count=10,
        sha256="a" * 64,
        expires_at=NOW + timedelta(days=20),
        **kwargs,
    )
    session.add(value)
    session.flush()
    return value


def distribution(session, item: LeadRequest, *, phone="2155550101") -> DistributionEvent:
    lead = Lead(phone=phone, title="Buckeye Dental", state="FL")
    session.add(lead)
    session.flush()
    event = DistributionEvent(
        lead_id=lead.id,
        agent_id=item.agent_id,
        request_id=item.id,
        phone=phone,
        title="Buckeye Dental",
        state="FL",
    )
    session.add(event)
    session.flush()
    return event


@pytest.fixture
def client(session):
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(session):
    agency = Agency(slug="gulf-coast", name="Gulf Coast Agency", active=True)
    session.add(agency)
    session.flush()
    northstar = customer(
        session, "northstar-insurance", billing_enabled=True,
        lead_rate_cents_per_thousand=500,
    )
    harbor = customer(
        session, "harbor-insurance", agency_id=agency.id, states=("FL", "TX"),
        billing_enabled=True, lead_rate_cents_per_thousand=500,
    )
    lakeside = customer(session, "lakeside-brokers")
    ridgeline = customer(session, "ridgeline-benefits")
    pinecrest = customer(session, "pinecrest-agency")
    gulfshore = customer(session, "gulfshore-advisors")
    summit = customer(session, "summit-benefits", lead_database="db2")
    quiet = customer(session, "quiet-customer")
    unset = customer(session, "unset-customer", states=(), account=False)
    former = customer(session, "former-customer", active=False)

    # Northstar: an approval and a held, billed batch.
    approval = batch(
        session, northstar, "pending", lead_count=25_000, hours_ago=2,
        is_billed=True, lead_rate_cents_per_thousand=500, billing_amount_cents=12_500,
    )
    session.add(
        BatchHold(request_id=approval.id, customer_id=northstar.id, amount_cents=12_500)
    )
    session.add(
        CreditLedgerEntry(
            customer_id=northstar.id, kind="purchase", amount_cents=53_750,
            reason="Card purchase", actor_user_id="system:stripe",
        )
    )
    # Pinecrest: delivery failed.
    failed = batch(session, pinecrest, "failed", hours_ago=3)
    artifact(session, failed, delivery_status="failed", delivery_attempts=2)
    # Ridgeline: waiting and short.
    batch(
        session, ridgeline, "waiting_inventory", lead_count=100_000,
        hours_ago=60, available_count=68_410,
    )
    # Harbor and Lakeside: a pending conflict.
    harbor_open = batch(session, harbor, "processing", lead_count=50_000, hours_ago=4)
    lakeside_open = batch(session, lakeside, "approved", hours_ago=5)
    session.add(
        InventoryConflict(
            older_request_id=lakeside_open.id,
            newer_request_id=harbor_open.id,
            inventory_snapshot={},
            snapshot_checksum="c" * 64,
            status="pending",
        )
    )
    # Gulfshore: an open report against a delivered batch.
    delivered = batch(
        session, gulfshore, "delivered", lead_count=69_666, hours_ago=48,
        delivered_at=NOW - timedelta(hours=40),
    )
    event = distribution(session, delivered)
    session.add(
        LeadReport(
            distribution_event_id=event.id,
            customer_id=gulfshore.id,
            reason="invalid_phone",
            details="Disconnected",
        )
    )
    # Summit: busy and fine.
    batch(
        session, summit, "delivered", lead_count=450_000, hours_ago=72,
        delivered_at=NOW - timedelta(hours=70),
    )
    batch(
        session, summit, "delivered", lead_count=10_000, hours_ago=24 * 45,
        delivered_at=NOW - timedelta(days=44),
    )
    session.add(
        CustomerAvailabilitySnapshot(
            customer_id=harbor.id, available=412_880,
            computed_at=NOW - timedelta(hours=6),
        )
    )
    session.commit()
    return {
        "agency": agency,
        "northstar": northstar,
        "harbor": harbor,
        "lakeside": lakeside,
        "ridgeline": ridgeline,
        "pinecrest": pinecrest,
        "gulfshore": gulfshore,
        "summit": summit,
        "quiet": quiet,
        "unset": unset,
        "former": former,
        "approval": approval,
        "failed": failed,
        "harbor_open": harbor_open,
        "lakeside_open": lakeside_open,
        "delivered": delivered,
    }


def rows_by_slug(result) -> dict:
    return {row.slug: row for row in result.customers}


def first_need(row) -> str:
    return row.needs[0].label if row.needs else ""


def test_needs_name_what_each_customer_waits_on(session, seeded):
    rows = rows_by_slug(build_customer_directory(session))
    approval, failed = seeded["approval"].number, seeded["failed"].number
    assert first_need(rows["northstar-insurance"]) == f"Approve #{approval}"
    assert first_need(rows["pinecrest-agency"]) == f"Re-notify #{failed}"
    ridgeline = rows["ridgeline-benefits"]
    assert first_need(ridgeline).endswith("short 31,590")
    assert first_need(rows["harbor-insurance"]) == "Conflict with Lakeside Brokers"
    assert first_need(rows["lakeside-brokers"]) == "Conflict with Harbor Insurance"
    assert first_need(rows["gulfshore-advisors"]) == "Report · invalid phone"
    assert first_need(rows["unset-customer"]) == "Setup · no user account"
    assert [need.label for need in rows["unset-customer"].needs] == [
        "Setup · no user account",
        "Setup · no appointed states",
    ]
    assert rows["summit-benefits"].needs == []
    assert rows["northstar-insurance"].needs[0].href == f"/app/admin/batches/{approval}"


def test_rows_sort_by_need_then_by_delivered_leads(session, seeded):
    result = build_customer_directory(session)
    slugs = [row.slug for row in result.customers]
    needing = [row.slug for row in result.customers if row.needs]
    assert slugs[: len(needing)] == needing
    assert needing[0] == "northstar-insurance"
    assert needing[1] == "pinecrest-agency"
    assert needing[2] == "ridgeline-benefits"
    assert needing[-1] == "unset-customer"
    # Nothing waiting: the busiest Customer first.
    assert slugs[len(needing)] == "summit-benefits"
    # Deactivated Customers are not in the default view.
    assert "former-customer" not in slugs
    assert result.counts.active == 9
    assert result.counts.deactivated == 1
    assert result.total == 10


def test_rows_carry_money_batches_and_deliveries(session, seeded):
    rows = rows_by_slug(build_customer_directory(session))
    northstar = rows["northstar-insurance"]
    assert northstar.billed is True
    assert northstar.balance_cents == 53_750 - 12_500
    assert northstar.held_cents == 12_500
    assert northstar.held_for == [seeded["approval"].number]
    assert northstar.lead_rate_cents_per_thousand == 500
    assert northstar.open_batches == 1
    assert northstar.open_batch.number == seeded["approval"].number
    assert northstar.open_batch.stage == "awaiting_approval"
    summit = rows["summit-benefits"]
    assert summit.billed is False
    # Only the last 30 days count.
    assert summit.delivered_30d == 450_000
    assert summit.delivered_30d_batches == 1
    assert summit.last_delivery_at is not None
    harbor = rows["harbor-insurance"]
    assert harbor.available == 412_880
    assert harbor.available_as_of is not None
    assert rows["quiet-customer"].available is None


def test_filters_narrow_by_database_billing_needs_and_status(session, seeded):
    db2 = build_customer_directory(session, database="db2")
    assert [row.slug for row in db2.customers] == ["summit-benefits"]
    billed = build_customer_directory(session, billing="billed")
    assert {row.slug for row in billed.customers} == {
        "northstar-insurance",
        "harbor-insurance",
    }
    unbilled = build_customer_directory(session, billing="not_billed")
    assert "northstar-insurance" not in {row.slug for row in unbilled.customers}
    needs = build_customer_directory(session, needs_only=True)
    assert all(row.needs for row in needs.customers)
    assert "summit-benefits" not in {row.slug for row in needs.customers}
    former = build_customer_directory(session, status="deactivated")
    assert [row.slug for row in former.customers] == ["former-customer"]
    # A deactivated Customer is not expected to work: no setup needs.
    assert former.customers[0].needs == []
    agency = build_customer_directory(session, agency_id=seeded["agency"].id)
    assert [row.slug for row in agency.customers] == ["harbor-insurance"]


def test_directory_endpoint_reads_the_new_filters(session, seeded, client):
    body = client.get(
        "/api/admin/customers/directory",
        params={"database": "db1", "billing": "billed", "needs_only": "true"},
    ).json()
    assert [row["slug"] for row in body["customers"]] == ["northstar-insurance", "harbor-insurance"]
    assert body["filters"]["database"] == "db1"
    assert body["filters"]["billing"] == "billed"
    assert body["filters"]["needs_only"] is True
    assert body["counts"] == {"active": 9, "deactivated": 1}
    row = body["customers"][0]
    assert row["needs"][0]["kind"] == "approval"
    assert row["balance_cents"] == 41_250
    everyone = client.get("/api/admin/customers/directory", params={"status": "all"}).json()
    assert everyone["matched"] == 10


def test_summary_matches_the_table_row(session, seeded, client):
    harbor = seeded["harbor"]
    body = client.get(f"/api/admin/customers/{harbor.id}/summary").json()
    assert body["needs"] == [
        {
            "kind": "conflict",
            "label": "Conflict with Lakeside Brokers",
            "href": body["needs"][0]["href"],
        }
    ]
    assert body["needs"][0]["href"].startswith("/app/admin/fulfillment/conflicts/")
    assert body["available"]["count"] == 412_880
    assert body["openBatches"]["count"] == 1
    assert body["openBatches"]["first"]["number"] == seeded["harbor_open"].number
    assert body["openBatches"]["first"]["stage"] == "generating"
    assert body["database"] == "db1"
    assert body["states"] == ["FL", "TX"]
    assert body["balance"]["billed"] is True
    assert body["customerSince"] is not None
    assert body["delivered"] == {"windowDays": 30, "leads": 0, "batches": 0}
    assert body["leadReports"] == {"open": 0, "total": 0}

    gulfshore = client.get(f"/api/admin/customers/{seeded['gulfshore'].id}/summary").json()
    assert gulfshore["leadReports"] == {"open": 1, "total": 1}
    assert gulfshore["delivered"]["leads"] == 69_666
    assert client.get("/api/admin/customers/999999/summary").status_code == 404


def test_lead_reports_list_the_customers_reports_with_their_batch(session, seeded, client):
    body = client.get(f"/api/admin/customers/{seeded['gulfshore'].id}/lead-reports").json()
    assert len(body) == 1
    report = body[0]
    assert report["reasonLabel"] == "Invalid phone"
    assert report["statusLabel"] == "Open"
    assert report["batch"] == {
        "number": seeded["delivered"].number,
        "href": f"/app/admin/batches/{seeded['delivered'].number}",
    }
    assert report["lead"]["phone"] == "2155550101"
    assert report["href"].startswith("/app/admin/fulfillment/reports/")
    assert client.get(f"/api/admin/customers/{seeded['quiet'].id}/lead-reports").json() == []


def test_changing_the_lead_database_is_audited_and_wakes_rotation(session, seeded, client):
    harbor = seeded["harbor"]
    missing = client.put(
        f"/api/admin/customers/{harbor.id}/lead-database",
        json={"lead_database": "db2", "reason": ""},
    )
    assert missing.status_code == 422

    changed = client.put(
        f"/api/admin/customers/{harbor.id}/lead-database",
        json={"lead_database": "db2", "reason": "Moving to uploaded leads"},
    )
    assert changed.status_code == 200
    assert changed.json() == {"customerId": harbor.id, "leadDatabase": "db2", "changed": True}
    session.expire_all()
    assert session.get(Agent, harbor.id).lead_database == "db2"
    # The snapshot counted DB1, so it no longer stands.
    assert session.get(CustomerAvailabilitySnapshot, harbor.id) is None
    entry = session.scalar(
        select(AuditEntry).where(AuditEntry.action == "customer_lead_database_changed")
    )
    assert entry is not None
    assert entry.reason == "Moving to uploaded leads"
    assert entry.target_id == str(harbor.id)
    assert session.scalar(select(Job).where(Job.kind == "fulfill_round_robin")) is not None

    same = client.put(
        f"/api/admin/customers/{harbor.id}/lead-database",
        json={"lead_database": "db2", "reason": "Again"},
    )
    assert same.json()["changed"] is False
    assert client.put(
        "/api/admin/customers/999999/lead-database",
        json={"lead_database": "db1", "reason": "x"},
    ).status_code == 404


def test_operator_billing_names_the_actor_and_the_batch(session, seeded, client):
    northstar = seeded["northstar"]
    session.add(
        CreditLedgerEntry(
            customer_id=northstar.id, kind="batch_charge", amount_cents=-500,
            reason="Batch charge", actor_user_id="system:allocation",
            batch_request_id=seeded["approval"].id,
        )
    )
    session.add(
        CreditLedgerEntry(
            customer_id=northstar.id, kind="admin_adjustment", amount_cents=100,
            reason="Goodwill", actor_user_id=str(uuid.uuid4()),
        )
    )
    session.commit()
    body = client.get(f"/api/admin/customers/{northstar.id}/billing").json()
    by_reason = {entry["reason"]: entry for entry in body["ledger"]}
    assert by_reason["Card purchase"]["actorLabel"] == "Stripe"
    assert by_reason["Card purchase"]["batchNumber"] is None
    assert by_reason["Batch charge"]["actorLabel"] == "System"
    assert by_reason["Batch charge"]["batchNumber"] == seeded["approval"].number
    assert by_reason["Goodwill"]["actorLabel"] == "Operator"


def test_one_customers_batches_read_newest_first_with_their_cost(session, seeded, client):
    northstar = seeded["northstar"]
    older = batch(
        session, northstar, "delivered", hours_ago=24 * 3,
        delivered_at=NOW - timedelta(days=2),
    )
    session.commit()
    body = client.get(
        "/api/admin/batches", params={"customer": northstar.id, "status": "all"}
    ).json()
    assert [row["number"] for row in body["items"]] == [
        seeded["approval"].number,
        older.number,
    ]
    assert body["items"][0]["cost"] == {"billed": True, "amountCents": 12_500, "hold": "active"}
    assert body["items"][1]["cost"] == {"billed": False}
    assert body["counts"]["delivered"] == 1
