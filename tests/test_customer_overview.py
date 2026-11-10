"""The Customer's Home read: /api/me/overview."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.billing import batch_cost_cents
from jawnix.customer_overview import (
    build_customer_home,
    covers_leads,
    month_start_for,
    parse_month_start,
)
from jawnix.database import get_db
from jawnix.models import (
    Agency,
    Agent,
    BatchArtifact,
    BatchHold,
    CreditLedgerEntry,
    CustomerProfile,
    LeadRequest,
    RequestStatus,
    UserAccount,
)


def _authenticate(session, user_id: uuid.UUID) -> TestClient:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="customer@example.com",
        role="customer",
        csrf="test",
    )
    return TestClient(app)


def _home(session, user_id: uuid.UUID, **params) -> dict:
    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview", params=params)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    return response.json()


def _customer(
    session,
    *,
    licensed_states: list[str] | None = None,
    rate: int | None = None,
):
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"home-{user_id}",
        name="Northstar Insurance",
        licensed_states=licensed_states or [],
        billing_enabled=rate is not None,
        lead_rate_cents_per_thousand=rate,
    )
    profile = CustomerProfile(
        user_id=user_id,
        email="customer@example.com",
        first_name="River",
        last_name="Morgan",
        licensed_states=licensed_states or [],
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
    return user_id, customer, profile, account


def _request(
    session,
    user_id,
    customer,
    *,
    lead_count: int,
    status: str,
    states: list[str] | None = None,
    created_at: datetime | None = None,
    delivered_at: datetime | None = None,
    approved_at: datetime | None = None,
    cost_cents: int | None = None,
) -> LeadRequest:
    item = LeadRequest(
        user_id=user_id,
        agent=customer,
        lead_count=lead_count,
        states_snapshot=states or ["TX"],
        state_mode="all_saved",
        delivery_email="customer@example.com",
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
        approved_at=approved_at,
        delivered_at=delivered_at,
        is_billed=cost_cents is not None,
        lead_rate_cents_per_thousand=500 if cost_cents is not None else None,
        billing_amount_cents=cost_cents,
    )
    session.add(item)
    session.flush()
    return item


def _artifact(session, tmp_path, request, *, expires_at: datetime):
    path = tmp_path / f"{request.id}.csv"
    path.write_text("phone,title,state\n2125550100,Example,TX\n")
    artifact = BatchArtifact(
        request=request,
        path=str(path),
        filename="customer_batch.csv",
        row_count=request.lead_count,
        byte_count=path.stat().st_size,
        sha256="a" * 64,
        expires_at=expires_at,
    )
    session.add(artifact)
    return artifact


def test_a_new_customer_sees_an_empty_home_with_nothing_to_alert(session):
    user_id, _, _, _ = _customer(session, licensed_states=["FL", "TX"])
    session.commit()

    home = _home(session, user_id)

    assert home["customer_name"] == "Northstar Insurance"
    assert home["appointed_states"] == ["FL", "TX"]
    assert home["can_request"] is True
    assert home["billing"] is None
    assert home["alerts"] == []
    assert home["in_progress"] == []
    assert home["recent_batches"] == []
    assert home["batch_count"] == 0
    assert home["month"]["delivered_leads"] == 0
    assert home["month"]["delivered_batches"] == 0


def test_recent_batches_offer_live_files_and_link_expired_ones(session, tmp_path):
    user_id, customer, _, _ = _customer(session, licensed_states=["FL", "TX"])
    now = datetime.now(timezone.utc)
    live = _request(
        session, user_id, customer,
        lead_count=50_000, states=["TX", "FL"],
        status=RequestStatus.delivered.value,
        created_at=now - timedelta(days=2), delivered_at=now - timedelta(days=2),
        cost_cents=25_000,
    )
    expired = _request(
        session, user_id, customer,
        lead_count=20_000,
        status=RequestStatus.delivered.value,
        created_at=now - timedelta(days=40), delivered_at=now - timedelta(days=39),
    )
    _request(
        session, user_id, customer, lead_count=500,
        status=RequestStatus.rejected.value,
    )
    _artifact(session, tmp_path, live, expires_at=now + timedelta(days=28))
    _artifact(session, tmp_path, expired, expires_at=now - timedelta(days=9))
    session.commit()

    home = _home(session, user_id)

    assert home["batch_count"] == 3
    assert [batch["id"] for batch in home["recent_batches"]] == [
        str(live.id),
        str(expired.id),
    ]
    first, second = home["recent_batches"]
    assert first["states"] == ["FL", "TX"]
    assert first["lead_count"] == 50_000
    assert first["cost_cents"] == 25_000
    assert first["file_available"] is True
    assert first["download_href"] == f"/api/me/batch-requests/{live.id}/artifact"
    assert first["href"] == f"/app/batches/{live.number}"
    assert first["can_request_copy"] is False
    assert second["cost_cents"] is None
    assert second["file_available"] is False
    assert second["download_href"] is None
    assert second["href"] == f"/app/batches/{expired.number}"
    # An expired file can be copied again from Home.
    assert second["can_request_copy"] is True
    assert second["copy_pending"] is False


def test_home_lists_at_most_five_recent_batches(session):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"])
    now = datetime.now(timezone.utc)
    for day in range(7):
        _request(
            session, user_id, customer, lead_count=100 + day,
            status=RequestStatus.delivered.value,
            delivered_at=now - timedelta(days=day),
        )
    session.commit()

    home = _home(session, user_id)

    assert [batch["lead_count"] for batch in home["recent_batches"]] == [
        100, 101, 102, 103, 104,
    ]
    assert home["batch_count"] == 7


def test_an_undownloaded_file_expiring_this_week_raises_an_alert(session, tmp_path):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"])
    now = datetime.now(timezone.utc)
    expiring = _request(
        session, user_id, customer, lead_count=50_000,
        status=RequestStatus.delivered.value,
        delivered_at=now - timedelta(days=27),
    )
    later = _request(
        session, user_id, customer, lead_count=10_000,
        status=RequestStatus.delivered.value,
        delivered_at=now - timedelta(days=10),
    )
    _artifact(session, tmp_path, expiring, expires_at=now + timedelta(days=3))
    _artifact(session, tmp_path, later, expires_at=now + timedelta(days=20))
    session.commit()

    home = _home(session, user_id)

    assert [alert["kind"] for alert in home["alerts"]] == ["file_expiring"]
    batch = home["alerts"][0]["batch"]
    assert batch["id"] == str(expiring.id)
    assert batch["lead_count"] == 50_000
    assert batch["download_href"] == (
        f"/api/me/batch-requests/{expiring.id}/artifact"
    )
    assert batch["file_expires_at"] is not None


def test_a_downloaded_file_raises_no_expiry_alert_even_after_account_replacement(
    session,
    tmp_path,
):
    """Downloads belong to the Customer, so a replaced account changes nothing."""

    user_id, customer, _, account = _customer(session, licensed_states=["TX"])
    now = datetime.now(timezone.utc)
    downloaded = _request(
        session, user_id, customer, lead_count=500,
        status=RequestStatus.delivered.value,
        delivered_at=now - timedelta(days=27),
    )
    waiting = _request(
        session, user_id, customer, lead_count=250,
        status=RequestStatus.delivered.value,
        delivered_at=now - timedelta(days=26),
    )
    _artifact(session, tmp_path, downloaded, expires_at=now + timedelta(days=2))
    _artifact(session, tmp_path, waiting, expires_at=now + timedelta(days=4))
    session.commit()

    client = _authenticate(session, user_id)
    try:
        assert client.get(
            f"/api/me/batch-requests/{downloaded.id}/artifact"
        ).status_code == 200
    finally:
        app.dependency_overrides.clear()

    replacement_id = uuid.uuid4()
    account.active = False
    account.replaced_at = now
    account.replaced_by_auth_user_id = replacement_id
    session.flush()
    session.add_all(
        [
            CustomerProfile(
                user_id=replacement_id,
                email="replacement@example.com",
                licensed_states=["TX"],
                agent=customer,
                mapping_confirmed_at=now,
            ),
            UserAccount(
                auth_user_id=replacement_id,
                email="replacement@example.com",
                customer=customer,
                active=True,
            ),
        ]
    )
    session.commit()

    home = _home(session, replacement_id)

    assert [
        alert["batch"]["id"]
        for alert in home["alerts"]
        if alert["kind"] == "file_expiring"
    ] == [str(waiting.id)]


@pytest.mark.parametrize(
    ("status", "approval", "preparing", "can_cancel"),
    [
        (RequestStatus.pending.value, ("Waiting for approval", "current"), "upcoming", True),
        (RequestStatus.approved.value, ("Approved", "complete"), "current", True),
        (RequestStatus.processing.value, ("Approved", "complete"), "current", False),
        (RequestStatus.waiting_inventory.value, ("Approved", "complete"), "current", True),
        (RequestStatus.generated.value, ("Approved", "complete"), "current", False),
    ],
)
def test_a_batch_in_progress_shows_its_steps(
    session, status, approval, preparing, can_cancel
):
    user_id, customer, _, _ = _customer(session, licensed_states=["FL"])
    now = datetime.now(timezone.utc)
    item = _request(
        session, user_id, customer, lead_count=25_000, states=["FL"],
        status=status,
        created_at=now - timedelta(hours=1),
        approved_at=None if status == RequestStatus.pending.value else now,
    )
    item.available_count = 987_654
    item.status_message = "internal-known-state-material"
    session.commit()

    home = _home(session, user_id)

    assert len(home["in_progress"]) == 1
    panel = home["in_progress"][0]
    assert panel["batch"]["id"] == str(item.id)
    assert panel["batch"]["lead_count"] == 25_000
    assert panel["can_cancel"] is can_cancel
    assert panel["held_cents"] is None
    steps = panel["steps"]
    assert [step["key"] for step in steps] == [
        "submitted", "approval", "preparing", "ready",
    ]
    assert steps[0]["state"] == "complete"
    assert steps[0]["occurred_at"] is not None
    assert (steps[1]["label"], steps[1]["state"]) == approval
    assert steps[2]["label"] == "Preparing your leads"
    assert steps[2]["state"] == preparing
    assert (steps[3]["label"], steps[3]["state"]) == ("Ready to download", "upcoming")
    assert panel["note"]
    # Supply stays hidden, and internal state never reaches the Customer.
    body = str(home)
    assert "987654" not in body and "987,654" not in body
    assert "internal-known-state-material" not in body


def test_closed_requests_are_not_in_progress(session):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"])
    for status in (
        RequestStatus.rejected.value,
        RequestStatus.canceled.value,
        RequestStatus.delivered.value,
    ):
        _request(session, user_id, customer, lead_count=100, status=status)
    session.commit()

    assert _home(session, user_id)["in_progress"] == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ("no_states", "no_appointed_states"),
        ("unconfirmed", "account_pending"),
        ("unmapped", "account_pending"),
        ("agency_inactive", "account_inactive"),
    ],
)
def test_unfinished_setup_raises_one_alert(session, change, problem):
    user_id, customer, profile, _ = _customer(session, licensed_states=["TX"])
    if change == "no_states":
        profile.licensed_states = []
    elif change == "unconfirmed":
        profile.mapping_confirmed_at = None
    elif change == "unmapped":
        profile.agent = None
    elif change == "agency_inactive":
        customer.agency = Agency(slug="paused", name="Paused Agency", active=False)
    session.commit()

    home = _home(session, user_id)

    assert home["can_request"] is False
    assert [
        (alert["kind"], alert["problem"]) for alert in home["alerts"]
    ] == [("setup_incomplete", problem)]


def test_billing_reads_the_balance_hold_rate_and_monthly_spend(session):
    user_id, customer, _, _ = _customer(session, licensed_states=["FL", "TX"], rate=500)
    now = datetime(2026, 11, 9, 14, 41, tzinfo=timezone.utc)
    month_start = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
    last_month = _request(
        session, user_id, customer, lead_count=50_000,
        status=RequestStatus.delivered.value,
        created_at=datetime(2026, 10, 30, 12, tzinfo=timezone.utc),
        delivered_at=datetime(2026, 11, 1, 2, tzinfo=timezone.utc),
        cost_cents=25_000,
    )
    this_month = [
        _request(
            session, user_id, customer, lead_count=count,
            status=RequestStatus.delivered.value,
            created_at=datetime(2026, 11, day, 9, tzinfo=timezone.utc),
            delivered_at=datetime(2026, 11, day, 10, tzinfo=timezone.utc),
            cost_cents=count // 2,
        )
        for count, day in ((50_000, 1), (100_000, 7))
    ]
    held = _request(
        session, user_id, customer, lead_count=25_000, states=["FL"],
        status=RequestStatus.pending.value,
        created_at=datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc),
        cost_cents=12_500,
    )
    session.add_all(
        [
            CreditLedgerEntry(
                customer_id=customer.id, kind="purchase", amount_cents=130_000,
                actor_user_id="system:stripe",
                created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            ),
            CreditLedgerEntry(
                customer_id=customer.id, kind="batch_charge", amount_cents=-25_000,
                actor_user_id="system:allocation", batch_request_id=last_month.id,
                created_at=datetime(2026, 11, 1, 2, tzinfo=timezone.utc),
            ),
            *[
                CreditLedgerEntry(
                    customer_id=customer.id, kind="batch_charge",
                    amount_cents=-item.billing_amount_cents,
                    actor_user_id="system:allocation", batch_request_id=item.id,
                    created_at=item.delivered_at,
                )
                for item in this_month
            ],
            BatchHold(
                request=held, customer_id=customer.id, amount_cents=12_500,
                status="active",
            ),
        ]
    )
    session.commit()

    home = build_customer_home(
        session,
        user_id=user_id,
        profile=session.get(CustomerProfile, user_id),
        month_start=month_start,
        now=now,
    ).model_dump(mode="json")

    # $1,300.00 bought − $250.00 − $250.00 − $500.00 charged = $300.00;
    # $125.00 of it is held for the pending batch.
    assert home["billing"] == {
        "available_balance_cents": 17_500,
        "held_cents": 12_500,
        "held_for": [{"id": str(held.id), "number": held.number}],
        "lead_rate_cents_per_thousand": 500,
        "covers_leads": 35_000,
        "spent_this_month_cents": 75_000,
    }
    assert home["month"] == {
        "starts_at": "2026-11-01T04:00:00Z",
        "delivered_leads": 150_000,
        "delivered_batches": 2,
    }
    assert home["in_progress"][0]["held_cents"] == 12_500
    # $175.00 covers the latest 25,000-lead batch ($125.00), so no alert.
    assert home["alerts"] == []


def test_a_balance_short_of_the_latest_batch_raises_a_low_balance_alert(session):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"], rate=500)
    _request(
        session, user_id, customer, lead_count=50_000,
        status=RequestStatus.delivered.value,
        delivered_at=datetime.now(timezone.utc),
        cost_cents=25_000,
    )
    session.add(
        CreditLedgerEntry(
            customer_id=customer.id, kind="purchase", amount_cents=3_750,
            actor_user_id="system:stripe",
        )
    )
    session.commit()

    home = _home(session, user_id)

    assert home["billing"]["available_balance_cents"] == 3_750
    assert home["billing"]["covers_leads"] == 7_500
    assert home["alerts"] == [
        {
            "kind": "low_balance",
            "batch": None,
            "problem": None,
            "reference_lead_count": 50_000,
        }
    ]


def test_an_empty_wallet_with_no_batches_is_low(session):
    user_id, _, _, _ = _customer(session, licensed_states=["TX"], rate=500)
    session.commit()

    home = _home(session, user_id)

    assert home["billing"]["covers_leads"] == 0
    assert [
        (alert["kind"], alert["reference_lead_count"]) for alert in home["alerts"]
    ] == [("low_balance", None)]


def test_unbilled_customers_have_no_billing_or_balance_alert(session):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"], rate=500)
    customer.billing_enabled = False
    session.commit()

    home = _home(session, user_id)

    assert home["billing"] is None
    assert home["alerts"] == []


def test_a_billed_customer_on_db2_sees_their_price_and_wallet(session):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"], rate=500)
    customer.lead_database = "db2"
    session.commit()

    home = _home(session, user_id)

    assert home["billing"] is not None
    assert home["billing"]["lead_rate_cents_per_thousand"] == 500


def test_the_browser_month_start_decides_this_month(session):
    user_id, customer, _, _ = _customer(session, licensed_states=["TX"])
    now = datetime.now(timezone.utc)
    utc_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    local_month = utc_month + timedelta(hours=5)
    # Delivered after the UTC month began but before the local one did.
    _request(
        session, user_id, customer, lead_count=1_000,
        status=RequestStatus.delivered.value,
        delivered_at=utc_month + timedelta(hours=2),
    )
    session.commit()

    utc = _home(session, user_id)
    local = _home(session, user_id, month_start=local_month.isoformat())
    nonsense = _home(session, user_id, month_start="not-a-date")

    assert utc["month"]["delivered_leads"] == 1_000
    if now >= local_month:
        assert local["month"]["delivered_leads"] == 0
    assert nonsense["month"]["delivered_leads"] == 1_000


def test_month_start_falls_back_to_the_utc_month_when_implausible():
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    utc_month = datetime(2026, 11, 1, tzinfo=timezone.utc)
    local = datetime(2026, 11, 1, 5, tzinfo=timezone.utc)

    assert month_start_for(None, now) == utc_month
    assert month_start_for(local, now) == local
    assert month_start_for(now + timedelta(hours=1), now) == utc_month
    assert month_start_for(now - timedelta(days=40), now) == utc_month
    assert parse_month_start("2026-11-01T04:00:00.000Z") == datetime(
        2026, 11, 1, 4, tzinfo=timezone.utc
    )
    assert parse_month_start("2026-11-01T00:00:00") == utc_month
    assert parse_month_start("tomorrow") is None


@pytest.mark.parametrize("rate", [100, 333, 500, 1_250, 2_000])
@pytest.mark.parametrize("available", [0, 1, 2, 99, 4_125, 41_250, 1_000_001])
def test_covers_leads_is_the_largest_batch_the_balance_can_pay_for(rate, available):
    leads = covers_leads(available, rate)

    if leads:
        assert batch_cost_cents(leads, rate) <= available
    assert batch_cost_cents(leads + 1, rate) > available


def test_home_refuses_a_replaced_or_deactivated_user_account(session):
    user_id, _, _, account = _customer(session, licensed_states=["TX"])
    account.active = False
    session.commit()

    client = _authenticate(session, user_id)
    try:
        response = client.get("/api/me/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
