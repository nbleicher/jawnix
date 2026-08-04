from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.allocation import allocate_request
from jawnix.api import app
from jawnix.auth import Principal, require_admin, require_principal
from jawnix.database import get_db
from jawnix.billing import batch_cost_cents, release_batch_hold
from jawnix.models import (
    Agent,
    AuditEntry,
    BatchHold,
    CreditLedgerEntry,
    CustomerProfile,
    Lead,
    LeadRequest,
    RequestStatus,
    UserAccount,
)


ADMIN_ID = uuid.UUID("64e97212-5eb5-4ec9-a63d-40fb9fb05c4b")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _bind_database(session) -> None:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override


def _as_admin(session) -> TestClient:
    _bind_database(session)
    principal = Principal(
        user_id=ADMIN_ID,
        email="admin@example.com",
        role="admin",
        audience="admin",
        csrf="test",
    )
    app.dependency_overrides[require_admin] = lambda: principal
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


def _as_customer(session, user_id: uuid.UUID) -> TestClient:
    _bind_database(session)
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="customer@example.com",
        role="customer",
        audience="customer",
        csrf="test",
    )
    return TestClient(app)


def _customer(session, name: str = "Billing Customer"):
    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"billing-{suffix}",
        name=name,
        licensed_states=["TX"],
    )
    profile = CustomerProfile(
        user_id=user_id,
        email=f"billing-{suffix}@example.com",
        licensed_states=["TX"],
        agent=customer,
        mapping_confirmed_at=datetime.now(timezone.utc),
    )
    account = UserAccount(
        auth_user_id=user_id,
        email=profile.email,
        customer=customer,
        active=True,
    )
    session.add_all([customer, profile, account])
    session.flush()
    return user_id, customer


def _configure(
    session,
    customer: Agent,
    *,
    enabled: bool,
    rate: int | None,
) -> dict:
    response = _as_admin(session).put(
        f"/api/admin/customers/{customer.id}/billing",
        json={
            "billing_enabled": enabled,
            "lead_rate_cents_per_thousand": rate,
            "reason": "Billing contract changed.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _adjust(session, customer: Agent, amount_cents: int) -> dict:
    response = _as_admin(session).post(
        f"/api/admin/customers/{customer.id}/billing/adjustments",
        json={
            "amount_cents": amount_cents,
            "reason": "Reconciled by finance.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _submit(
    session,
    user_id: uuid.UUID,
    *,
    lead_count: int,
    key: str,
):
    return _as_customer(session, user_id).post(
        "/api/me/batch-requests",
        json={
            "idempotency_key": key,
            "lead_count": lead_count,
            "state_mode": "selected",
            "states": ["TX"],
        },
    )


def test_positive_billed_batches_have_a_one_cent_floor():
    assert batch_cost_cents(1, 100) == 1
    assert batch_cost_cents(4, 100) == 1
    assert batch_cost_cents(0, 100) == 0


def test_billed_submission_is_refused_when_available_balance_cannot_cover_hold(
    session,
):
    user_id, customer = _customer(session)
    _configure(session, customer, enabled=True, rate=2_000)
    _adjust(session, customer, 999)

    response = _submit(
        session,
        user_id,
        lead_count=500,
        key="billing-refusal-0001",
    )

    assert response.status_code == 409
    assert "Batch Hold" in response.json()["detail"]
    assert session.scalar(select(func.count(LeadRequest.id))) == 0
    assert session.scalar(select(func.count(BatchHold.id))) == 0


def test_hold_captures_with_distribution_and_releases_on_cancel_or_reject(
    session,
    settings,
):
    user_id, customer = _customer(session)
    _configure(session, customer, enabled=True, rate=1_000)
    _adjust(session, customer, 100)

    capture_response = _submit(
        session,
        user_id,
        lead_count=10,
        key="billing-capture-0001",
    )
    capture_id = uuid.UUID(capture_response.json()["request"]["id"])
    capture_request = session.get(LeadRequest, capture_id)
    capture_request.status = RequestStatus.approved.value
    session.add_all(
        [
            Lead(
                phone=f"214555{index:04d}",
                title=f"Capture {index}",
                state="TX",
            )
            for index in range(10)
        ]
    )

    allocate_request(session, capture_id, settings)
    session.commit()

    captured = session.scalar(
        select(BatchHold).where(BatchHold.request_id == capture_id)
    )
    charge = session.scalar(
        select(CreditLedgerEntry).where(
            CreditLedgerEntry.batch_request_id == capture_id
        )
    )
    assert captured.status == "captured"
    assert charge.kind == "batch_charge"
    assert charge.amount_cents == -10

    cancel_response = _submit(
        session,
        user_id,
        lead_count=10,
        key="billing-cancel-0002",
    )
    cancel_id = uuid.UUID(cancel_response.json()["request"]["id"])
    assert (
        _as_customer(session, user_id)
        .post(f"/api/me/batch-requests/{cancel_id}/cancel")
        .status_code
        == 200
    )

    reject_response = _submit(
        session,
        user_id,
        lead_count=10,
        key="billing-reject-0003",
    )
    reject_id = uuid.UUID(reject_response.json()["request"]["id"])
    assert (
        _as_admin(session)
        .post(
            f"/api/admin/requests/{reject_id}/reject",
            json={"reason": "The request did not pass review."},
        )
        .status_code
        == 200
    )

    released = list(
        session.scalars(
            select(BatchHold).where(
                BatchHold.request_id.in_([cancel_id, reject_id])
            )
        )
    )
    assert {hold.status for hold in released} == {"released"}
    assert (
        session.scalar(
            select(func.count(CreditLedgerEntry.id)).where(
                CreditLedgerEntry.batch_request_id.in_(
                    [cancel_id, reject_id]
                )
            )
        )
        == 0
    )

    wallet = _as_customer(session, user_id).get("/api/me/billing").json()
    assert wallet["balanceCents"] == 90
    assert wallet["activeHoldsCents"] == 0
    assert wallet["availableBalanceCents"] == 90


def test_retry_after_a_failed_allocation_re_arms_the_released_hold(
    session,
    settings,
):
    """Releasing the hold on failure must not make the request unretryable."""

    user_id, customer = _customer(session)
    _configure(session, customer, enabled=True, rate=1_000)
    _adjust(session, customer, 100)

    response = _submit(
        session,
        user_id,
        lead_count=10,
        key="billing-retry-0001",
    )
    request_id = uuid.UUID(response.json()["request"]["id"])
    request = session.get(LeadRequest, request_id)
    request.status = RequestStatus.failed.value
    release_batch_hold(session, request)
    session.commit()

    wallet = _as_customer(session, user_id).get("/api/me/billing").json()
    assert wallet["activeHoldsCents"] == 0

    retried = _as_admin(session).post(
        f"/api/admin/requests/{request_id}/retry",
        json={"reason": "The transient allocation fault is resolved."},
    )
    assert retried.status_code == 200, retried.text
    session.expire_all()

    hold = session.scalar(
        select(BatchHold).where(BatchHold.request_id == request_id)
    )
    assert hold.status == "active"
    assert hold.released_at is None
    wallet = _as_customer(session, user_id).get("/api/me/billing").json()
    assert wallet["activeHoldsCents"] == 10
    assert wallet["availableBalanceCents"] == 90

    # The re-armed hold is the one allocation captures, so the retry completes.
    session.add_all(
        [
            Lead(
                phone=f"214556{index:04d}",
                title=f"Retry {index}",
                state="TX",
            )
            for index in range(10)
        ]
    )
    session.commit()
    allocate_request(session, request_id, settings)
    session.commit()

    session.expire_all()
    hold = session.scalar(
        select(BatchHold).where(BatchHold.request_id == request_id)
    )
    assert hold.status == "captured"


def test_retry_is_refused_when_the_wallet_no_longer_covers_the_hold(session):
    user_id, customer = _customer(session)
    _configure(session, customer, enabled=True, rate=1_000)
    _adjust(session, customer, 100)

    response = _submit(
        session,
        user_id,
        lead_count=10,
        key="billing-retry-0002",
    )
    request_id = uuid.UUID(response.json()["request"]["id"])
    request = session.get(LeadRequest, request_id)
    request.status = RequestStatus.failed.value
    release_batch_hold(session, request)
    session.commit()
    _adjust(session, customer, -95)

    retried = _as_admin(session).post(
        f"/api/admin/requests/{request_id}/retry",
        json={"reason": "Trying again after the fault."},
    )
    assert retried.status_code == 409
    assert "available balance" in retried.json()["detail"]

    session.expire_all()
    hold = session.scalar(
        select(BatchHold).where(BatchHold.request_id == request_id)
    )
    assert hold.status == "released"
    assert session.get(LeadRequest, request_id).status == (
        RequestStatus.failed.value
    )


def test_billed_or_free_status_is_frozen_across_customer_toggle_flips(
    session,
    settings,
):
    user_id, customer = _customer(session)
    _configure(session, customer, enabled=True, rate=1_000)
    _adjust(session, customer, 100)
    billed_response = _submit(
        session,
        user_id,
        lead_count=10,
        key="frozen-billed-0001",
    )
    billed_id = uuid.UUID(billed_response.json()["request"]["id"])

    _configure(session, customer, enabled=False, rate=None)
    free_response = _submit(
        session,
        user_id,
        lead_count=10,
        key="frozen-free-0002",
    )
    free_id = uuid.UUID(free_response.json()["request"]["id"])
    _configure(session, customer, enabled=True, rate=2_000)

    billed_request = session.get(LeadRequest, billed_id)
    free_request = session.get(LeadRequest, free_id)
    assert (
        billed_request.is_billed,
        billed_request.lead_rate_cents_per_thousand,
        billed_request.billing_amount_cents,
    ) == (True, 1_000, 10)
    assert (
        free_request.is_billed,
        free_request.lead_rate_cents_per_thousand,
        free_request.billing_amount_cents,
    ) == (False, None, None)

    billed_request.status = RequestStatus.approved.value
    free_request.status = RequestStatus.approved.value
    session.add_all(
        [
            Lead(
                phone=f"469555{index:04d}",
                title=f"Frozen {index}",
                state="TX",
            )
            for index in range(20)
        ]
    )
    allocate_request(session, billed_id, settings)
    allocate_request(session, free_id, settings)
    session.commit()

    charges = list(
        session.scalars(
            select(CreditLedgerEntry).where(
                CreditLedgerEntry.kind == "batch_charge"
            )
        )
    )
    assert [(entry.batch_request_id, entry.amount_cents) for entry in charges] == [
        (billed_id, -10)
    ]
    assert session.scalar(
        select(BatchHold).where(BatchHold.request_id == free_id)
    ) is None


def test_admin_adjustment_and_billing_reads_are_audited(session):
    user_id, customer = _customer(session)

    missing_rate = _as_admin(session).put(
        f"/api/admin/customers/{customer.id}/billing",
        json={
            "billing_enabled": True,
            "lead_rate_cents_per_thousand": None,
            "reason": "Trying to enable billing.",
        },
    )
    assert missing_rate.status_code == 422

    _configure(session, customer, enabled=True, rate=500)
    adjusted = _adjust(session, customer, 2_500)
    assert adjusted["balanceCents"] == 2_500
    assert adjusted["ledger"][0]["kind"] == "admin_adjustment"
    assert adjusted["ledger"][0]["reason"] == "Reconciled by finance."

    admin_read = _as_admin(session).get(
        f"/api/admin/customers/{customer.id}/billing"
    )
    customer_read = _as_customer(session, user_id).get("/api/me/billing")
    assert admin_read.status_code == customer_read.status_code == 200
    assert admin_read.json()["balanceCents"] == 2_500
    assert customer_read.json()["ledger"] == admin_read.json()["ledger"]

    entries = list(
        session.scalars(
            select(AuditEntry)
            .where(AuditEntry.target_id == str(customer.id))
            .order_by(AuditEntry.created_at)
        )
    )
    assert [entry.action for entry in entries] == [
        "customer_billing_updated",
        "credit_wallet_adjusted",
        "credit_wallet_viewed",
    ]
    adjustment = entries[1]
    assert adjustment.reason == "Reconciled by finance."
    assert adjustment.details["amountCents"] == 2_500
    assert adjustment.actor_user_id == str(ADMIN_ID)


def test_credit_ledger_entries_are_append_only(session):
    _, customer = _customer(session)
    _adjust(session, customer, 500)
    entry = session.scalar(select(CreditLedgerEntry))

    entry.amount_cents = 700
    with pytest.raises(ValueError, match="append-only"):
        session.flush()
    session.rollback()
