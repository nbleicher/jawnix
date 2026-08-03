from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jawnix.api import app
from jawnix.auth import Principal, require_principal
from jawnix.config import Settings, get_settings
from jawnix.database import get_db
from jawnix.models import (
    Agent,
    CreditLedgerEntry,
    CreditPurchase,
    CustomerProfile,
    UserAccount,
)
from jawnix.stripe_client import CheckoutSession


WEBHOOK_SECRET = "whsec_test_credit_purchase"


@dataclass
class FakeStripeClient:
    """In-memory Stripe client used as the suite's only Stripe test seam."""

    webhook_secret: str = WEBHOOK_SECRET
    secret_key: str = "sk_test_fake"
    sessions: dict[str, CheckoutSession] = field(default_factory=dict)
    created: list[dict[str, object]] = field(default_factory=list)

    def create_checkout_session(
        self,
        *,
        amount_cents: int,
        customer_email: str | None,
        metadata: dict[str, str],
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        session_id = f"cs_test_{uuid.uuid4().hex}"
        session = CheckoutSession(
            id=session_id,
            url=f"https://checkout.stripe.test/pay/{session_id}",
        )
        self.sessions[session_id] = session
        self.created.append(
            {
                "amount_cents": amount_cents,
                "customer_email": customer_email,
                "metadata": dict(metadata),
                "success_url": success_url,
                "cancel_url": cancel_url,
                "session": session,
            }
        )
        return session

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature_header: str,
    ) -> dict:
        if not signature_header:
            raise ValueError("Missing Stripe signature.")
        verify_stripe_signature(
            payload,
            signature_header,
            self.webhook_secret,
        )
        return json.loads(payload.decode("utf-8"))

    def sign(self, payload: bytes, *, timestamp: int | None = None) -> str:
        return sign_stripe_payload(
            payload,
            self.webhook_secret,
            timestamp=timestamp,
        )


def sign_stripe_payload(
    payload: bytes,
    secret: str,
    *,
    timestamp: int | None = None,
) -> str:
    stamped = int(time.time() if timestamp is None else timestamp)
    signed = hmac.new(
        secret.encode("utf-8"),
        f"{stamped}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={stamped},v1={signed}"


def verify_stripe_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
) -> None:
    items: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        key, _, value = part.partition("=")
        items.setdefault(key.strip(), []).append(value.strip())
    try:
        stamped = int(items["t"][0])
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError("Malformed Stripe signature.") from exc
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{stamped}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    candidates = items.get("v1", [])
    if not any(
        hmac.compare_digest(expected, candidate) for candidate in candidates
    ):
        raise ValueError("Invalid Stripe signature.")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _bind(session, settings: Settings) -> None:
    def database_override():
        yield session

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings


def _as_customer(session, settings: Settings, user_id: uuid.UUID) -> TestClient:
    _bind(session, settings)
    app.dependency_overrides[require_principal] = lambda: Principal(
        user_id=user_id,
        email="customer@example.com",
        role="customer",
        audience="customer",
        csrf="test",
    )
    return TestClient(app)


def _billing_settings(fake: FakeStripeClient) -> Settings:
    settings = Settings(
        JAWNIX_BATCH_DIR="/tmp/jawnix-batches",
        JAWNIX_COOKIE_SECURE=False,
        JAWNIX_SESSION_SECRET="test-secret-at-least-long-enough",
        JAWNIX_PUBLIC_BASE_URL="https://jawnix.test",
        STRIPE_SECRET_KEY=fake.secret_key,
        STRIPE_WEBHOOK_SECRET=fake.webhook_secret,
    )
    settings.stripe_client = fake
    return settings


def _customer(session, *, billing_enabled: bool = True):
    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    customer = Agent(
        slug=f"purchase-{suffix}",
        name="Purchase Customer",
        licensed_states=["TX"],
        billing_enabled=billing_enabled,
        lead_rate_cents_per_thousand=1_000 if billing_enabled else None,
    )
    profile = CustomerProfile(
        user_id=user_id,
        email=f"purchase-{suffix}@example.com",
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
    return user_id, customer, profile


def _checkout_completed_event(
    *,
    event_id: str,
    session_id: str,
    amount_cents: int,
    metadata: dict[str, str] | None = None,
) -> dict:
    return {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "amount_total": amount_cents,
                "payment_status": "paid",
                "metadata": metadata or {},
            }
        },
    }


def _post_webhook(
    client: TestClient,
    fake: FakeStripeClient,
    event: dict,
):
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    return client.post(
        "/api/billing/stripe/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": fake.sign(payload),
        },
    )


def test_credit_purchase_checkout_and_webhook_credits_wallet_once(session):
    fake = FakeStripeClient()
    settings = _billing_settings(fake)
    user_id, customer, profile = _customer(session)
    client = _as_customer(session, settings, user_id)

    created = client.post(
        "/api/me/billing/purchases",
        json={"amount_dollars": 25},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["checkoutUrl"].startswith("https://checkout.stripe.test/pay/")
    assert body["purchase"]["status"] == "processing"
    assert body["purchase"]["amountCents"] == 2_500
    assert fake.created[0]["amount_cents"] == 2_500
    assert fake.created[0]["customer_email"] == profile.email

    wallet = client.get("/api/me/billing").json()
    assert wallet["balanceCents"] == 0
    assert wallet["purchases"][0]["status"] == "processing"
    assert wallet["purchases"][0]["amountCents"] == 2_500

    session_id = fake.created[0]["session"].id
    purchase_id = body["purchase"]["id"]
    event = _checkout_completed_event(
        event_id="evt_test_credit_once",
        session_id=session_id,
        amount_cents=2_500,
        metadata={"purchase_id": purchase_id, "customer_id": str(customer.id)},
    )
    credited = _post_webhook(client, fake, event)
    assert credited.status_code == 200, credited.text
    assert credited.json() == {"ok": True}

    wallet = client.get("/api/me/billing").json()
    assert wallet["balanceCents"] == 2_500
    assert wallet["purchases"][0]["status"] == "completed"
    assert wallet["ledger"][0]["kind"] == "purchase"
    assert wallet["ledger"][0]["amountCents"] == 2_500
    assert session.scalar(select(func.count(CreditLedgerEntry.id))) == 1


def test_duplicate_stripe_event_does_not_double_credit(session):
    fake = FakeStripeClient()
    settings = _billing_settings(fake)
    user_id, _, _ = _customer(session)
    client = _as_customer(session, settings, user_id)

    created = client.post(
        "/api/me/billing/purchases",
        json={"amount_dollars": 10},
    ).json()
    session_id = fake.created[0]["session"].id
    event = _checkout_completed_event(
        event_id="evt_test_duplicate",
        session_id=session_id,
        amount_cents=1_000,
        metadata={"purchase_id": created["purchase"]["id"]},
    )

    first = _post_webhook(client, fake, event)
    second = _post_webhook(client, fake, event)
    assert first.status_code == second.status_code == 200
    assert second.json() == {"ok": True, "duplicate": True}
    assert client.get("/api/me/billing").json()["balanceCents"] == 1_000
    assert session.scalar(select(func.count(CreditLedgerEntry.id))) == 1


def test_stripe_webhook_rejects_bad_signature(session):
    fake = FakeStripeClient()
    settings = _billing_settings(fake)
    user_id, _, _ = _customer(session)
    client = _as_customer(session, settings, user_id)
    created = client.post(
        "/api/me/billing/purchases",
        json={"amount_dollars": 5},
    ).json()
    event = _checkout_completed_event(
        event_id="evt_test_bad_sig",
        session_id=fake.created[0]["session"].id,
        amount_cents=500,
        metadata={"purchase_id": created["purchase"]["id"]},
    )
    payload = json.dumps(event).encode("utf-8")
    response = client.post(
        "/api/billing/stripe/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": "t=1,v1=deadbeef",
        },
    )
    assert response.status_code == 401
    assert session.scalar(select(func.count(CreditLedgerEntry.id))) == 0
    assert session.scalar(select(CreditPurchase)).status == "processing"


def test_stripe_webhook_ignores_unknown_checkout_session(session):
    fake = FakeStripeClient()
    settings = _billing_settings(fake)
    user_id, _, _ = _customer(session)
    client = _as_customer(session, settings, user_id)

    event = _checkout_completed_event(
        event_id="evt_test_unknown_session",
        session_id="cs_test_unknown",
        amount_cents=900,
    )
    response = _post_webhook(client, fake, event)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "ignored": True}
    assert session.scalar(select(func.count(CreditLedgerEntry.id))) == 0
    assert session.scalar(select(func.count(CreditPurchase.id))) == 0


def test_purchase_stays_processing_until_webhook_lands(session):
    fake = FakeStripeClient()
    settings = _billing_settings(fake)
    user_id, _, _ = _customer(session)
    client = _as_customer(session, settings, user_id)

    created = client.post(
        "/api/me/billing/purchases",
        json={"amount_dollars": 40},
    )
    assert created.status_code == 201
    purchase = created.json()["purchase"]
    assert purchase["status"] == "processing"

    before = client.get("/api/me/billing").json()
    assert before["balanceCents"] == 0
    assert before["purchases"] == [
        {
            "id": purchase["id"],
            "amountCents": 4_000,
            "status": "processing",
            "createdAt": before["purchases"][0]["createdAt"],
            "completedAt": None,
        }
    ]
    assert before["ledger"] == []

    event = _checkout_completed_event(
        event_id="evt_test_processing_visibility",
        session_id=fake.created[0]["session"].id,
        amount_cents=4_000,
        metadata={"purchase_id": purchase["id"]},
    )
    assert _post_webhook(client, fake, event).status_code == 200

    after = client.get("/api/me/billing").json()
    assert after["balanceCents"] == 4_000
    assert after["purchases"][0]["status"] == "completed"
    assert after["purchases"][0]["completedAt"] is not None
    assert after["ledger"][0]["kind"] == "purchase"


def test_purchase_rejects_sub_dollar_amounts(session):
    fake = FakeStripeClient()
    settings = _billing_settings(fake)
    user_id, _, _ = _customer(session)
    client = _as_customer(session, settings, user_id)

    response = client.post(
        "/api/me/billing/purchases",
        json={"amount_dollars": 0},
    )
    assert response.status_code == 422
    assert fake.created == []
