"""Billing belongs to the Customer, not the Lead Database.

A billed Customer drawing from DB2 is priced at their own rate, buys credit
by card, and has each Batch held and then charged, exactly as on DB1.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jawnix.allocation import allocate_request
from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.billing import initialize_customer_billing
from jawnix.config import get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    BatchHold,
    CreditLedgerEntry,
    CustomerProfile,
    DistributionEvent,
    Lead,
    LeadRequest,
    RequestStatus,
    UserAccount,
)

from test_credit_purchase_api import FakeStripeClient, _billing_settings, _checkout_completed_event, _post_webhook
from test_lead_databases import db2_source

ADMIN_ID = uuid.UUID("64e97212-5eb5-4ec9-a63d-40fb9fb05c4b")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client(session, settings, principal: Principal) -> TestClient:
    def database():
        yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_principal] = lambda: principal
    if principal.role == "admin":
        app.dependency_overrides[require_admin] = lambda: principal
    return TestClient(app)


def _db2_customer(session):
    user_id = uuid.uuid4()
    customer = Agent(slug="upload-fed", name="Upload Fed Insurance", licensed_states=["TX"], lead_database="db2")
    profile = CustomerProfile(user_id=user_id, email="owner@upload-fed.example", licensed_states=["TX"],
                              agent=customer, mapping_confirmed_at=datetime.now(timezone.utc))
    session.add_all([customer, profile,
                     UserAccount(auth_user_id=user_id, email=profile.email, customer=customer, active=True)])
    session.flush()
    return user_id, customer


def test_a_billed_db2_customer_is_priced_buys_credit_and_is_held_then_charged(session, settings):
    fake = FakeStripeClient()
    api_settings = _billing_settings(fake)
    user_id, customer = _db2_customer(session)
    admin = Principal(user_id=ADMIN_ID, email="admin@example.com", role="admin", audience="admin", csrf="test")
    owner = Principal(user_id=user_id, email="owner@upload-fed.example", role="customer", audience="customer", csrf="test")

    # The operator can switch billing on while the Customer draws from DB2.
    configured = _client(session, api_settings, admin).put(
        f"/api/admin/customers/{customer.id}/billing",
        json={"billing_enabled": True, "lead_rate_cents_per_thousand": 1_000, "reason": "Signed a DB2 contract."},
    )
    assert configured.status_code == 200, configured.text

    client = _client(session, api_settings, owner)
    # Price: the Customer sees their own rate and wallet.
    wallet = client.get("/api/me/billing").json()
    assert wallet["billingEnabled"] and wallet["leadRateCentsPerThousand"] == 1_000

    # Purchase: a card payment credits the wallet once.
    started = client.post("/api/me/billing/purchases", json={"amount_dollars": 1})
    assert started.status_code == 201, started.text
    purchase = started.json()["purchase"]
    credited = _post_webhook(client, fake, _checkout_completed_event(
        event_id="evt_db2_purchase", session_id=fake.created[0]["session"].id, amount_cents=100,
        metadata={"purchase_id": purchase["id"], "customer_id": str(customer.id)},
    ))
    assert credited.status_code == 200, credited.text
    assert client.get("/api/me/billing").json()["balanceCents"] == 100

    # Hold: the submission is billed at the reviewed rate and holds its cost.
    submitted = client.post("/api/me/batch-requests", json={
        "idempotency_key": "db2-billed-batch-0001", "lead_count": 10, "state_mode": "selected", "states": ["TX"],
        "expected_lead_rate_cents_per_thousand": 1_000,
    })
    assert submitted.status_code == 201, submitted.text
    request_id = uuid.UUID(submitted.json()["request"]["id"])
    request = session.get(LeadRequest, request_id)
    assert (request.is_billed, request.lead_rate_cents_per_thousand, request.billing_amount_cents) == (True, 1_000, 10)
    hold = session.scalar(select(BatchHold).where(BatchHold.request_id == request_id))
    assert (hold.status, hold.amount_cents) == ("active", 10)
    assert client.get("/api/me/billing").json()["availableBalanceCents"] == 90

    # Capture: allocating from DB2 charges the held amount.
    leads = [Lead(phone=f"512555{index:04d}", title="", state="", in_db1=False) for index in range(10)]
    session.add_all(leads)
    session.flush()
    db2_source(session, leads, state="TX")
    request.status = RequestStatus.approved.value
    assert allocate_request(session, request_id, settings).allocated == 10
    session.commit()

    events = list(session.scalars(select(DistributionEvent).where(DistributionEvent.request_id == request_id)))
    assert len(events) == 10 and {event.inventory_database for event in events} == {"db2"}
    assert session.get(BatchHold, hold.id).status == "captured"
    charge = session.scalar(select(CreditLedgerEntry).where(CreditLedgerEntry.batch_request_id == request_id))
    assert (charge.kind, charge.amount_cents) == ("batch_charge", -10)
    wallet = client.get("/api/me/billing").json()
    assert (wallet["balanceCents"], wallet["activeHoldsCents"], wallet["availableBalanceCents"]) == (90, 0, 90)


@pytest.mark.parametrize("database", ["db1", "db2"])
def test_new_customers_start_billed_at_the_default_rate_on_either_database(settings, database):
    settings.billing_enabled = True
    settings.default_lead_rate_cents_per_thousand = 150
    customer = Agent(slug=f"new-{database}", name="New", licensed_states=["TX"], lead_database=database)

    initialize_customer_billing(customer, settings)

    assert customer.billing_enabled and customer.lead_rate_cents_per_thousand == 150


def test_a_batch_frozen_unbilled_on_db2_stays_free(session, settings):
    """Batches submitted while DB2 was unbilled keep their frozen terms."""
    user_id, customer = _db2_customer(session)
    customer.billing_enabled, customer.lead_rate_cents_per_thousand = True, 1_000
    legacy = LeadRequest(user_id=user_id, agent_id=customer.id, lead_count=2, states_snapshot=["TX"],
                         delivery_email="owner@upload-fed.example", status=RequestStatus.approved.value,
                         is_billed=False)
    leads = [Lead(phone=f"737555{index:04d}", title="", state="", in_db1=False) for index in range(2)]
    session.add_all([legacy, *leads])
    session.flush()
    db2_source(session, leads, state="TX")

    assert allocate_request(session, legacy.id, settings).allocated == 2
    session.commit()

    assert session.scalar(select(BatchHold).where(BatchHold.request_id == legacy.id)) is None
    assert session.scalar(select(CreditLedgerEntry).where(CreditLedgerEntry.batch_request_id == legacy.id)) is None
    assert (legacy.is_billed, legacy.billing_amount_cents) == (False, None)
