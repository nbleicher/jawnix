"""The Customer's Home read against disposable PostgreSQL."""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from jawnix.customer_overview import build_customer_home
from jawnix.database import Base
from jawnix.models import (
    Agent,
    BatchArtifact,
    BatchHold,
    CreditLedgerEntry,
    CustomerProfile,
    LeadRequest,
    RequestStatus,
)

DATABASE_URL = os.environ.get("JAWNIX_ACCEPTANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires disposable PostgreSQL")


@pytest.fixture
def home_engine():
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_home_reads_one_customers_batches_money_and_month(home_engine, tmp_path):
    now = datetime(2026, 11, 9, 14, 41, tzinfo=timezone.utc)
    month_start = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
    user_id = uuid.uuid4()
    with Session(home_engine, expire_on_commit=False) as db:
        customer = Agent(
            slug=f"home-{user_id}",
            name="Northstar Insurance",
            licensed_states=["FL", "TX"],
            billing_enabled=True,
            lead_rate_cents_per_thousand=500,
        )
        other = Agent(slug=f"other-{user_id}", name="Other Customer", licensed_states=["TX"])
        profile = CustomerProfile(
            user_id=user_id,
            email=f"{user_id}@example.test",
            licensed_states=["FL", "TX"],
            agent=customer,
            mapping_confirmed_at=now,
        )
        db.add_all([customer, other, profile])
        db.flush()

        def request(agent, lead_count, status, *, delivered_at=None, cost=None, created_at=None):
            item = LeadRequest(
                user_id=user_id,
                agent=agent,
                lead_count=lead_count,
                states_snapshot=["FL"],
                state_mode="all_saved",
                delivery_email=profile.email,
                status=status,
                created_at=created_at or now - timedelta(days=1),
                delivered_at=delivered_at,
                is_billed=cost is not None,
                lead_rate_cents_per_thousand=500 if cost is not None else None,
                billing_amount_cents=cost,
            )
            db.add(item)
            db.flush()
            return item

        before_month = request(
            customer, 50_000, RequestStatus.delivered.value,
            delivered_at=datetime(2026, 11, 1, 2, tzinfo=timezone.utc), cost=25_000,
            created_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
        )
        expiring = request(
            customer, 100_000, RequestStatus.delivered.value,
            delivered_at=datetime(2026, 11, 3, tzinfo=timezone.utc), cost=50_000,
            created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        pending = request(customer, 25_000, RequestStatus.pending.value, cost=12_500, created_at=now)
        # Another Customer's batch never appears.
        request(other, 9_999, RequestStatus.delivered.value, delivered_at=now)

        path = tmp_path / "batch.csv"
        path.write_text("phone,title,state\n")
        db.add_all(
            [
                BatchArtifact(
                    request=expiring, path=str(path), filename="batch.csv",
                    row_count=100_000, byte_count=1, sha256="a" * 64,
                    expires_at=now + timedelta(days=2),
                ),
                CreditLedgerEntry(
                    customer_id=customer.id, kind="purchase", amount_cents=100_000,
                    actor_user_id="system:stripe", created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                ),
                CreditLedgerEntry(
                    customer_id=customer.id, kind="batch_charge", amount_cents=-25_000,
                    actor_user_id="system:allocation", batch_request_id=before_month.id,
                    created_at=before_month.delivered_at,
                ),
                CreditLedgerEntry(
                    customer_id=customer.id, kind="batch_charge", amount_cents=-50_000,
                    actor_user_id="system:allocation", batch_request_id=expiring.id,
                    created_at=expiring.delivered_at,
                ),
                BatchHold(request=pending, customer_id=customer.id, amount_cents=12_500, status="active"),
            ]
        )
        db.commit()

        home = build_customer_home(
            db, user_id=user_id, profile=profile, month_start=month_start, now=now,
        )

    assert home.batch_count == 3
    assert [batch.id for batch in home.recent_batches] == [expiring.id, before_month.id]
    assert home.recent_batches[0].download_href == (
        f"/api/me/batch-requests/{expiring.id}/artifact"
    )
    assert home.month.delivered_leads == 100_000
    assert home.month.delivered_batches == 1
    assert home.billing is not None
    assert home.billing.available_balance_cents == 12_500
    assert home.billing.held_cents == 12_500
    assert [ref.id for ref in home.billing.held_for] == [pending.id]
    assert home.billing.spent_this_month_cents == 50_000
    assert home.billing.covers_leads == 25_000
    assert [alert.kind for alert in home.alerts] == ["file_expiring"]
    assert home.alerts[0].batch.id == expiring.id
    assert [item.batch.id for item in home.in_progress] == [pending.id]
    assert home.in_progress[0].held_cents == 12_500
