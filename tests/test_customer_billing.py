"""Account's Billing tab: the four numbers and one reconciling history."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.customer_billing import HISTORY_LIMIT, build_customer_billing
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    BatchHold,
    CreditLedgerEntry,
    CustomerProfile,
    LeadRequest,
    UserAccount,
)

NOW = datetime(2026, 11, 9, 14, 41, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _customer(session, *, billed: bool = True, database: str = "db1"):
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"northstar-{user_id.hex[:8]}",
        name="Northstar Insurance",
        licensed_states=["FL", "TX"],
        lead_database=database,
        billing_enabled=billed,
        lead_rate_cents_per_thousand=500 if billed else None,
    )
    profile = CustomerProfile(
        user_id=user_id,
        email="river@northstar.example",
        licensed_states=["FL", "TX"],
        agent=customer,
        mapping_confirmed_at=NOW,
    )
    session.add_all([
        customer,
        profile,
        UserAccount(auth_user_id=user_id, email=profile.email, customer=customer, active=True),
    ])
    session.flush()
    return user_id, customer


def _batch(session, customer, user_id, *, leads=25_000, status="approved", at=NOW):
    request = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=leads,
        states_snapshot=["FL"],
        delivery_email="river@northstar.example",
        status=status,
        is_billed=True,
        lead_rate_cents_per_thousand=500,
        billing_amount_cents=leads // 2,
        created_at=at,
    )
    session.add(request)
    session.flush()
    return request


def _entry(session, customer, kind, cents, at, *, request=None, reason=""):
    entry = CreditLedgerEntry(
        customer_id=customer.id,
        kind=kind,
        amount_cents=cents,
        reason=reason,
        actor_user_id="system",
        batch_request_id=request.id if request is not None else None,
        created_at=at,
    )
    session.add(entry)
    session.flush()
    return entry


def _hold(session, customer, request, cents, at, status="active"):
    hold = BatchHold(
        request_id=request.id,
        customer_id=customer.id,
        amount_cents=cents,
        status=status,
        created_at=at,
    )
    session.add(hold)
    session.flush()
    return hold


def _northstar(session):
    """The V9 story: credits bought, batches charged, one batch held."""

    user_id, customer = _customer(session)
    _entry(session, customer, "purchase", 28_750, NOW - timedelta(days=45))
    older = _batch(session, customer, user_id, status="delivered", at=NOW - timedelta(days=40))
    _entry(session, customer, "batch_charge", -25_000, NOW - timedelta(days=40), request=older)
    _entry(session, customer, "purchase", 75_000, NOW - timedelta(days=10))
    first = _batch(session, customer, user_id, status="delivered", at=NOW - timedelta(days=9))
    _entry(session, customer, "batch_charge", -50_000, NOW - timedelta(days=8), request=first)
    second = _batch(session, customer, user_id, status="delivered", at=NOW - timedelta(days=3))
    _entry(session, customer, "batch_charge", -25_000, NOW - timedelta(days=2), request=second)
    _entry(session, customer, "purchase", 50_000, NOW - timedelta(minutes=11))
    held = _batch(session, customer, user_id, at=NOW)
    _hold(session, customer, held, 12_500, NOW)
    # A canceled batch's hold was released: it never moved the balance.
    canceled = _batch(session, customer, user_id, status="canceled", at=NOW - timedelta(days=1))
    _hold(session, customer, canceled, 9_900, NOW - timedelta(days=1), status="released")
    session.commit()
    return user_id, customer, {"older": older, "first": first, "second": second, "held": held}


def test_history_walks_the_available_balance_through_holds_and_ledger(session):
    _, customer, batches = _northstar(session)

    billing = build_customer_billing(session, customer, now=NOW)

    assert billing is not None
    assert billing.available_balance_cents == 41_250
    assert billing.held_cents == 12_500
    assert [batch.number for batch in billing.held_for] == [batches["held"].number]
    assert billing.held_for[0].href == f"/app/batches/{batches['held'].id}"
    assert billing.lead_rate_cents_per_thousand == 500
    assert [
        (row.kind, row.amount_cents, row.balance_after_cents)
        for row in billing.history
    ] == [
        ("hold", -12_500, 41_250),
        ("purchase", 50_000, 53_750),
        ("charge", -25_000, 3_750),
        ("charge", -50_000, 28_750),
        ("purchase", 75_000, 78_750),
        ("charge", -25_000, 3_750),
        ("purchase", 28_750, 28_750),
    ]
    # The oldest row's balance less its amount is where the wallet began.
    oldest = billing.history[-1]
    assert oldest.balance_after_cents - oldest.amount_cents == 0
    assert billing.history[0].batch.number == batches["held"].number
    assert billing.history[2].batch.href == f"/app/batches/{batches['second'].id}"
    assert billing.history[1].batch is None
    assert billing.history_complete


def test_spent_counts_batch_charges_in_the_last_30_days(session):
    _, customer, _ = _northstar(session)

    billing = build_customer_billing(session, customer, now=NOW)

    assert billing.spent_cents == 75_000
    assert billing.spent_batches == 2
    assert billing.spent_since == NOW - timedelta(days=30)


def test_adjustments_carry_the_operator_reason(session):
    _, customer = _customer(session)
    _entry(session, customer, "admin_adjustment", 375, NOW, reason="Refund for a bad lead")

    billing = build_customer_billing(session, customer, now=NOW)

    assert [(row.kind, row.note) for row in billing.history] == [
        ("adjustment", "Refund for a bad lead"),
    ]


def test_history_keeps_the_newest_rows_and_says_when_older_ones_are_left_off(session):
    _, customer = _customer(session)
    for index in range(HISTORY_LIMIT + 1):
        _entry(session, customer, "purchase", 100, NOW - timedelta(minutes=index))

    billing = build_customer_billing(session, customer, now=NOW)

    assert len(billing.history) == HISTORY_LIMIT
    assert not billing.history_complete
    assert billing.history[0].balance_after_cents == (HISTORY_LIMIT + 1) * 100
    assert billing.history[-1].balance_after_cents == 200


@pytest.mark.parametrize("database", ["db1", "db2"])
def test_unbilled_customers_have_no_billing_tab(session, database):
    _, customer = _customer(session, billed=False, database=database)

    assert build_customer_billing(session, customer, now=NOW) is None


def test_a_billed_customer_on_db2_has_a_billing_tab(session):
    _, customer = _customer(session, billed=True, database="db2")

    billing = build_customer_billing(session, customer, now=NOW)

    assert billing is not None
    assert billing.lead_rate_cents_per_thousand == customer.lead_rate_cents_per_thousand


def _as_customer(session, user_id) -> TestClient:
    def database():
        yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="river@northstar.example",
        role="customer",
        audience="customer",
        csrf="test",
    )
    return TestClient(app)


def test_billing_summary_endpoint_serves_the_signed_in_customer(session):
    user_id, _, batches = _northstar(session)

    response = _as_customer(session, user_id).get("/api/me/billing/summary")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["held_cents"] == 12_500
    assert body["held_for"][0]["number"] == batches["held"].number
    assert body["history"][0]["kind"] == "hold"
    assert body["history"][0]["batch"]["href"] == f"/app/batches/{batches['held'].id}"


def test_billing_summary_is_absent_for_an_unbilled_customer(session):
    user_id, _ = _customer(session, billed=False)

    response = _as_customer(session, user_id).get("/api/me/billing/summary")

    assert response.status_code == 404
    assert response.json() == {"detail": "Billing is not set up for this account."}
