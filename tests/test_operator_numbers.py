"""The operator's headline numbers row (Overview now, Today next).

Seam: seeded operational records in; ``operator_numbers`` and the
``numbers`` block of the Operations overview out.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from jawnix import operations_overview as overview
from jawnix.api import app
from jawnix.auth import Principal, require_admin
from jawnix.database import get_db
from jawnix.inventory_snapshots import store_inventory_snapshot
from jawnix.models import (
    Agency,
    Agent,
    BatchHold,
    CreditLedgerEntry,
    CustomerProfile,
    CustomerRegistration,
    ExclusionList,
    InventoryConflict,
    Job,
    JobStatus,
    LeadRequest,
    SourceNicheMapping,
    UserAccount,
    UserAccountInvitation,
)
from jawnix.operator_numbers import operator_numbers


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def customer(session, slug: str, **kwargs) -> Agent:
    item = Agent(slug=slug, name=slug.title(), **kwargs)
    session.add(item)
    session.flush()
    return item


def account(session, item: Agent) -> None:
    session.add(
        UserAccount(
            auth_user_id=uuid.uuid4(),
            customer_id=item.id,
            email=f"{item.slug}@example.com",
        )
    )
    session.flush()


def batch(
    session,
    owner: Agent,
    status: str,
    *,
    lead_count: int = 100,
    created_at: datetime = NOW - timedelta(hours=1),
    delivered_at: datetime | None = None,
) -> LeadRequest:
    user_id = uuid.uuid4()
    session.add(
        CustomerProfile(
            user_id=user_id,
            email=f"{user_id}@example.com",
            licensed_states=["TX"],
            agent=owner,
        )
    )
    session.flush()
    item = LeadRequest(
        user_id=user_id,
        agent=owner,
        lead_count=lead_count,
        states_snapshot=["TX"],
        state_mode="all_saved",
        delivery_email="ops@example.com",
        status=status,
        created_at=created_at,
        delivered_at=delivered_at,
    )
    session.add(item)
    session.flush()
    return item


def ledger(session, owner: Agent, kind: str, cents: int, created_at: datetime):
    charged = (
        batch(session, owner, "delivered", delivered_at=created_at - timedelta(days=60))
        if kind == "batch_charge"
        else None
    )
    session.add(
        CreditLedgerEntry(
            customer_id=owner.id,
            kind=kind,
            amount_cents=cents,
            reason="Fixture",
            actor_user_id="system:test",
            batch_request_id=charged.id if charged else None,
            created_at=created_at,
        )
    )
    session.flush()


def test_empty_platform_reports_zeroes_and_no_snapshot(session):
    numbers = operator_numbers(session, now=NOW)

    assert numbers.inventory.db1 is None and numbers.inventory.db2 is None
    assert numbers.open_batches.total == 0
    assert numbers.delivered.leads == 0
    assert numbers.customers.active == 0
    assert numbers.credit_balance.available_cents == 0
    assert numbers.credits_used.cents == numbers.credits_used.prior_cents == 0
    assert numbers.decisions.waiting == 0
    assert numbers.decisions.oldest_at is None
    assert numbers.window_days == 30


def test_every_tile_counts_only_what_its_label_says(session):
    store_inventory_snapshot(
        session,
        "db1",
        {
            "allRows": 12,
            "suppressed": 1,
            "held": 1,
            "excluded": 1,
            "usable": 9,
            "usableByState": {"TX": 9},
        },
        computed_at=NOW - timedelta(hours=6),
    )

    paused = Agency(slug="paused", name="Paused", active=False)
    session.add(paused)
    session.flush()
    ready = customer(
        session, "ready", licensed_states=["TX"], billing_enabled=True,
        lead_rate_cents_per_thousand=500,
    )
    account(session, ready)
    invited = customer(
        session, "invited", licensed_states=["TX"], billing_enabled=True,
        lead_rate_cents_per_thousand=500,
    )
    session.add(
        UserAccountInvitation(
            customer_id=invited.id,
            auth_user_id=uuid.uuid4(),
            email="invited@example.com",
            status="pending",
            invited_by="admin",
            reason="First User Account.",
        )
    )
    in_paused_agency = customer(
        session, "agency-paused", licensed_states=["TX"], agency=paused,
    )
    account(session, in_paused_agency)
    # Billing switched on but drawing from DB2: not a Billed Customer.
    db2_customer = customer(
        session, "db2-customer", licensed_states=["TX"], billing_enabled=True,
        lead_rate_cents_per_thousand=500, lead_database="db2",
    )
    account(session, db2_customer)
    deactivated = customer(
        session, "deactivated", active=False, licensed_states=["TX"],
    )

    # Credit balance: ledger minus active holds, Billed Customers only.
    ledger(session, ready, "purchase", 50_000, NOW - timedelta(days=40))
    ledger(session, ready, "batch_charge", -1_250, NOW - timedelta(days=2))
    ledger(session, ready, "batch_charge", -1_000, NOW - timedelta(days=45))
    ledger(session, invited, "admin_adjustment", 2_000, NOW - timedelta(days=1))
    ledger(session, db2_customer, "purchase", 99_900, NOW - timedelta(days=3))
    ledger(session, db2_customer, "batch_charge", -250, NOW - timedelta(days=3))

    pending = batch(session, ready, "pending", created_at=NOW - timedelta(hours=3))
    session.add(
        BatchHold(
            request_id=pending.id,
            customer_id=ready.id,
            amount_cents=500,
            status="active",
        )
    )
    batch(session, ready, "waiting_inventory")
    batch(session, ready, "approved")
    failed = batch(session, invited, "failed", created_at=NOW - timedelta(days=2))
    batch(session, ready, "rejected")
    batch(
        session, ready, "delivered", lead_count=25_000,
        delivered_at=NOW - timedelta(days=1),
    )
    batch(
        session, invited, "delivered", lead_count=50_000,
        delivered_at=NOW - timedelta(days=29),
    )
    batch(
        session, invited, "delivered", lead_count=10_000,
        delivered_at=NOW - timedelta(days=31),
    )

    # The failed Batch Request is one decision; its failed job is not another.
    session.add_all(
        [
            Job(
                kind="deliver_request",
                request_id=failed.id,
                status=JobStatus.failed.value,
            ),
            Job(
                kind="run_scraper",
                status=JobStatus.failed.value,
                created_at=NOW - timedelta(days=4),
            ),
            Job(kind="run_scraper", status=JobStatus.complete.value),
            InventoryConflict(
                older_request_id=pending.id,
                newer_request_id=failed.id,
                inventory_snapshot={},
                snapshot_checksum="b" * 64,
                status="pending",
                created_at=NOW - timedelta(hours=5),
            ),
            ExclusionList(
                customer_id=ready.id,
                uploaded_by="customer",
                exclusion_type="dnc",
                filename="dnc.csv",
                storage_path="/tmp/dnc.csv",
                status="pending_confirmation",
                created_at=NOW - timedelta(hours=8),
            ),
            SourceNicheMapping(
                segment_key="TX::roofer",
                state="TX",
                keyword="roofer",
                niche="Roofing",
                confirmed=False,
                updated_at=NOW - timedelta(hours=9),
            ),
            SourceNicheMapping(
                segment_key="TX::dentist",
                state="TX",
                keyword="dentist",
                niche="Dental",
                confirmed=True,
            ),
            CustomerRegistration(
                auth_user_id=uuid.uuid4(),
                email="new@example.com",
                first_name="New",
                last_name="Customer",
                phone="2155550100",
                licensed_states=["TX"],
                status="awaiting_review",
                created_at=NOW - timedelta(hours=10),
            ),
        ]
    )
    session.flush()

    numbers = operator_numbers(session, now=NOW)

    assert numbers.inventory.db1.usable == 9
    assert numbers.inventory.db1.as_of == NOW - timedelta(hours=6)
    assert numbers.inventory.db2 is None
    assert numbers.open_batches.model_dump(by_alias=True) == {
        "total": 4,
        "awaitingApproval": 1,
        "shortOfInventory": 1,
    }
    assert numbers.delivered.model_dump() == {
        "leads": 75_000,
        "batches": 2,
        "customers": 2,
    }
    # ready has an account; invited waits on acceptance; agency-paused sits in
    # a deactivated Agency; the DB2 Customer is ready; deactivated is excluded.
    assert numbers.customers.active == 4
    assert numbers.customers.with_setup_problems == 2
    assert numbers.credit_balance.model_dump(by_alias=True) == {
        "availableCents": 50_000 - 1_250 - 1_000 - 500 + 2_000,
        "billedCustomers": 2,
    }
    # Batch Charges across every Customer, split into the two 30-day windows.
    assert numbers.credits_used.cents == 1_250 + 250
    assert numbers.credits_used.prior_cents == 1_000

    kinds = {
        kind.kind: (kind.count, kind.oldest_at)
        for kind in numbers.decisions.kinds
    }
    assert kinds == {
        "approval": (1, NOW - timedelta(hours=3)),
        "failedBatch": (1, NOW - timedelta(days=2)),
        "inventoryConflict": (1, NOW - timedelta(hours=5)),
        "leadReport": (0, None),
        "failedJob": (1, NOW - timedelta(days=4)),
        "scrapeAnomaly": (0, None),
        "nicheMapping": (1, NOW - timedelta(hours=9)),
        "exclusionList": (1, NOW - timedelta(hours=8)),
        "registration": (1, NOW - timedelta(hours=10)),
    }
    assert numbers.decisions.waiting == 7
    assert numbers.decisions.oldest_at == NOW - timedelta(days=4)
    assert deactivated.active is False


def test_overview_keeps_its_queues_when_the_numbers_fail(session, monkeypatch):
    def broken(_db):
        raise RuntimeError("private topology")

    monkeypatch.setattr(overview, "operator_numbers", broken)
    result = overview.operations_overview(session)

    assert result.numbers is None
    assert result.degraded is False
    assert [source.status for source in result.sources] == [
        "available",
        "available",
        "available",
    ]


def test_overview_endpoint_serves_the_numbers_in_camel_case(session):
    store_inventory_snapshot(
        session,
        "db2",
        {
            "allRows": 3,
            "suppressed": 0,
            "held": 0,
            "excluded": 0,
            "usable": 3,
            "usableByState": {"TN": 3},
        },
    )
    session.commit()

    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_admin] = lambda: Principal(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role="admin",
        csrf="test",
    )
    try:
        body = TestClient(app).get("/api/admin/operations-overview").json()
    finally:
        app.dependency_overrides.clear()

    numbers = body["numbers"]
    assert numbers["inventory"]["db1"] is None
    assert numbers["inventory"]["db2"]["usable"] == 3
    assert numbers["inventory"]["db2"]["asOf"]
    assert numbers["openBatches"] == {
        "total": 0,
        "awaitingApproval": 0,
        "shortOfInventory": 0,
    }
    assert numbers["creditBalance"] == {"availableCents": 0, "billedCustomers": 0}
    assert numbers["creditsUsed"] == {"cents": 0, "priorCents": 0}
    assert numbers["decisions"]["oldestAt"] is None
    assert {kind["kind"] for kind in numbers["decisions"]["kinds"]} >= {
        "approval",
        "failedBatch",
        "registration",
    }
