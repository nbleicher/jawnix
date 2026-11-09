"""Account's Billing tab read against disposable PostgreSQL."""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from jawnix.customer_billing import build_customer_billing
from jawnix.database import Base
from jawnix.models import Agent, BatchHold, CreditLedgerEntry, CustomerProfile, LeadRequest

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")


@pytest.fixture
def billing_engine():
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_billing_tab_reads_one_customers_holds_ledger_and_spending(billing_engine):
    now = datetime(2026, 11, 9, 14, 41, tzinfo=timezone.utc)
    user_id = uuid.uuid4()
    with Session(billing_engine, expire_on_commit=False) as db:
        customer = Agent(
            slug=f"billing-{user_id}",
            name="Northstar Insurance",
            licensed_states=["FL"],
            billing_enabled=True,
            lead_rate_cents_per_thousand=500,
        )
        other = Agent(
            slug=f"billing-other-{user_id}",
            name="Other Customer",
            licensed_states=["FL"],
            billing_enabled=True,
            lead_rate_cents_per_thousand=500,
        )
        profile = CustomerProfile(
            user_id=user_id,
            email=f"{user_id}@example.test",
            licensed_states=["FL"],
            agent=customer,
            mapping_confirmed_at=now,
        )
        db.add_all([customer, other, profile])
        db.flush()

        def batch(agent, status, at):
            item = LeadRequest(
                user_id=user_id,
                agent=agent,
                lead_count=25_000,
                states_snapshot=["FL"],
                state_mode="all_saved",
                delivery_email=profile.email,
                status=status,
                created_at=at,
                is_billed=True,
                lead_rate_cents_per_thousand=500,
                billing_amount_cents=12_500,
            )
            db.add(item)
            db.flush()
            return item

        def entry(agent, kind, cents, at, request=None, reason=""):
            db.add(CreditLedgerEntry(
                customer_id=agent.id,
                kind=kind,
                amount_cents=cents,
                reason=reason,
                actor_user_id="system",
                batch_request_id=request.id if request else None,
                created_at=at,
            ))
            db.flush()

        entry(customer, "purchase", 50_000, now - timedelta(days=40))
        old = batch(customer, "delivered", now - timedelta(days=35))
        entry(customer, "batch_charge", -12_500, now - timedelta(days=34), old)
        recent = batch(customer, "delivered", now - timedelta(days=3))
        entry(customer, "batch_charge", -12_500, now - timedelta(days=2), recent)
        entry(customer, "admin_adjustment", 500, now - timedelta(days=1), reason="Goodwill")
        held = batch(customer, "approved", now - timedelta(hours=1))
        db.add(BatchHold(request_id=held.id, customer_id=customer.id, amount_cents=12_500, status="active", created_at=now - timedelta(hours=1)))
        # Another Customer's money never appears.
        entry(other, "purchase", 99_900, now - timedelta(days=1))
        theirs = batch(other, "approved", now)
        db.add(BatchHold(request_id=theirs.id, customer_id=other.id, amount_cents=9_900, status="active", created_at=now))
        db.commit()

        billing = build_customer_billing(db, customer, now=now)

    assert billing is not None
    assert billing.available_balance_cents == 50_000 - 25_000 + 500 - 12_500
    assert billing.held_cents == 12_500
    assert [item.number for item in billing.held_for] == [held.number]
    assert [(row.kind, row.balance_after_cents) for row in billing.history] == [
        ("hold", 13_000),
        ("adjustment", 25_500),
        ("charge", 25_000),
        ("charge", 37_500),
        ("purchase", 50_000),
    ]
    assert billing.history[2].batch.number == recent.number
    assert billing.history[1].note == "Goodwill"
    assert (billing.spent_cents, billing.spent_batches) == (12_500, 1)
    assert billing.history_complete
