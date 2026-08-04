"""Minimal Stripe client seam for Credit Purchase checkout and webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import urlencode

import httpx

from .config import Settings


STRIPE_CHECKOUT_SESSIONS_URL = "https://api.stripe.com/v1/checkout/sessions"
SIGNATURE_TOLERANCE_SECONDS = 300


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str


class StripeClient(Protocol):
    """Internal Stripe boundary: create Checkout and verify webhooks."""

    def create_checkout_session(
        self,
        *,
        amount_cents: int,
        customer_email: str | None,
        metadata: Mapping[str, str],
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession: ...

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature_header: str,
    ) -> dict: ...


class StripeClientError(RuntimeError):
    """Raised when Stripe rejects a request or returns an unusable payload."""


class HttpStripeClient:
    """Production Stripe client built from secret-key and webhook-secret settings."""

    def __init__(self, *, secret_key: str, webhook_secret: str):
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret

    def create_checkout_session(
        self,
        *,
        amount_cents: int,
        customer_email: str | None,
        metadata: Mapping[str, str],
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        if not self._secret_key:
            raise StripeClientError("STRIPE_SECRET_KEY is not configured.")
        if amount_cents < 100 or amount_cents % 100 != 0:
            raise StripeClientError(
                "Credit Purchases must be whole dollars with a $1 floor."
            )
        form: dict[str, str] = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][product_data][name]": (
                "Jawnix Credit Purchase"
            ),
        }
        if customer_email:
            form["customer_email"] = customer_email
        for key, value in metadata.items():
            form[f"metadata[{key}]"] = value
        try:
            response = httpx.post(
                STRIPE_CHECKOUT_SESSIONS_URL,
                content=urlencode(form),
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise StripeClientError(
                "Stripe Checkout is temporarily unavailable."
            ) from exc
        if response.status_code >= 400:
            detail = _stripe_error_message(response)
            raise StripeClientError(
                detail or "Stripe Checkout Session creation failed."
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise StripeClientError(
                "Stripe returned an unusable Checkout response."
            ) from exc
        checkout_url = str(data.get("url") or "").strip()
        checkout_id = str(data.get("id") or "").strip()
        if not checkout_url or not checkout_id:
            raise StripeClientError(
                "Stripe did not return a Checkout Session URL."
            )
        return CheckoutSession(id=checkout_id, url=checkout_url)

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature_header: str,
    ) -> dict:
        if not self._webhook_secret:
            raise StripeClientError(
                "STRIPE_WEBHOOK_SECRET is not configured."
            )
        if not signature_header:
            raise ValueError("Missing Stripe signature.")
        verify_stripe_signature(
            payload,
            signature_header,
            self._webhook_secret,
        )
        return json.loads(payload.decode("utf-8"))


def get_stripe_client(settings: Settings) -> StripeClient:
    """Resolve the Stripe client injected on settings, else the HTTP client."""

    injected = getattr(settings, "stripe_client", None)
    if injected is not None:
        return injected
    return HttpStripeClient(
        secret_key=settings.stripe_secret_key,
        webhook_secret=settings.stripe_webhook_secret,
    )


def sign_stripe_payload(
    payload: bytes,
    secret: str,
    *,
    timestamp: int | None = None,
) -> str:
    stamped = int(time.time() if timestamp is None else timestamp)
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{stamped}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={stamped},v1={digest}"


def verify_stripe_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
    *,
    tolerance_seconds: int = SIGNATURE_TOLERANCE_SECONDS,
    now: int | None = None,
) -> None:
    items: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        key, _, value = part.partition("=")
        items.setdefault(key.strip(), []).append(value.strip())
    try:
        stamped = int(items["t"][0])
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError("Malformed Stripe signature.") from exc
    current = int(time.time() if now is None else now)
    if abs(current - stamped) > tolerance_seconds:
        raise ValueError("Stripe signature timestamp is outside tolerance.")
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


def _stripe_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or "").strip()
    return ""
