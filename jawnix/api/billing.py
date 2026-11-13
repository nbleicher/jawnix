from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ..auth import Principal, require_admin, require_principal
from ..billing import (
    BillingError,
    close_credit_purchase_for_session,
    complete_credit_purchase_for_session,
    configure_customer_billing,
    post_admin_adjustment,
    start_credit_purchase,
    wallet_view,
)
from ..checkout_return import checkout_return_urls
from ..config import Settings, get_settings
from ..stripe_client import StripeClientError, get_stripe_client
from ..customer_billing import CustomerBilling, build_customer_billing
from ..customer_record import operator_wallet
from ..database import get_db
from ..models import Customer, WebhookReceipt
from ..schemas import (
    CreditAdjustmentCreate,
    CreditPurchaseCreate,
    CustomerBillingUpdate,
)
from ._shared import _current_customer_profile, log
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/me/billing")
def get_my_billing(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    profile = _current_customer_profile(db, principal)
    if profile.customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    return wallet_view(db, profile.customer)


@router.get("/api/me/billing/summary", response_model=CustomerBilling)
def get_my_billing_summary(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
):
    """Account's Billing tab: balance, held, rate, spending, and history."""
    profile = _current_customer_profile(db, principal)
    summary = (
        build_customer_billing(db, profile.customer)
        if profile.customer is not None
        else None
    )
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail="Billing is not set up for this account.",
        )
    return summary


@router.post("/api/me/billing/purchases", status_code=201)
def create_credit_purchase(
    payload: CreditPurchaseCreate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.billing_enabled:
        raise HTTPException(status_code=503, detail="Stripe payments are temporarily unavailable.")
    profile = _current_customer_profile(db, principal)
    customer = profile.customer
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    stripe = get_stripe_client(settings)
    success_url, cancel_url = checkout_return_urls(
        settings.public_base_url, payload.return_to
    )
    try:
        purchase, checkout_url = start_credit_purchase(
            db,
            customer=customer,
            amount_dollars=payload.amount_dollars,
            customer_email=profile.email,
            stripe=stripe,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except BillingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    db.commit()
    wallet = wallet_view(db, customer)
    purchase_view = next(
        item for item in wallet["purchases"] if item["id"] == str(purchase.id)
    )
    return {
        "checkoutUrl": checkout_url,
        "purchase": purchase_view,
    }


@router.post("/api/billing/stripe/webhook")
async def stripe_billing_webhook(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.stripe_webhook_secret and settings.stripe_client is None:
        raise HTTPException(
            status_code=503,
            detail="Stripe webhook verification is not configured.",
        )
    body = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    stripe = get_stripe_client(settings)
    try:
        event = stripe.verify_webhook_signature(body, signature)
    except (ValueError, StripeClientError):
        raise HTTPException(
            status_code=401,
            detail="Invalid Stripe webhook signature.",
        ) from None
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        raise HTTPException(status_code=400, detail="Stripe event id is required.")
    try:
        with db.begin_nested():
            db.add(WebhookReceipt(provider="stripe", event_key=event_id))
            db.flush()
    except IntegrityError:
        return {"ok": True, "duplicate": True}

    event_type = str(event.get("type") or "")
    handled_types = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }
    if event_type not in handled_types:
        db.commit()
        return {"ok": True, "ignored": True}

    session_payload = (event.get("data") or {}).get("object") or {}
    checkout_session_id = str(session_payload.get("id") or "").strip()
    if not checkout_session_id:
        db.commit()
        return {"ok": True, "ignored": True}

    payment_status = str(session_payload.get("payment_status") or "")
    should_credit = (
        event_type == "checkout.session.async_payment_succeeded"
        or (
            event_type == "checkout.session.completed"
            and payment_status == "paid"
        )
    )
    try:
        if should_credit:
            purchase = complete_credit_purchase_for_session(
                db,
                stripe_checkout_session_id=checkout_session_id,
                settled_late=(
                    event_type == "checkout.session.async_payment_succeeded"
                ),
            )
        elif event_type in {
            "checkout.session.async_payment_failed",
            "checkout.session.expired",
        }:
            purchase = close_credit_purchase_for_session(
                db,
                stripe_checkout_session_id=checkout_session_id,
                status=(
                    "failed"
                    if event_type == "checkout.session.async_payment_failed"
                    else "expired"
                ),
            )
        else:
            db.commit()
            return {"ok": True, "pending": True}
    except BillingError as exc:
        # Stripe retries a non-2xx, so a refusal here repeats until someone
        # looks. Say which session and why in the log rather than leaving a
        # silent loop.
        log.warning(
            "Stripe %s for session %s was refused: %s",
            event_type,
            checkout_session_id,
            exc.detail,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    if purchase is None:
        db.commit()
        return {"ok": True, "ignored": True}
    db.commit()
    return {"ok": True}


@router.get("/api/admin/customers/{customer_id}/billing")
def admin_customer_billing(
    customer_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Customer was not found.")
    # Reads are not Activity: only consequential actions are audited.
    return operator_wallet(db, wallet_view(db, customer))


@router.put("/api/admin/customers/{customer_id}/billing")
@router.patch(
    "/api/admin/customers/{customer_id}/billing",
    include_in_schema=False,
)
def update_customer_billing(
    customer_id: int,
    payload: CustomerBillingUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        customer = configure_customer_billing(
            db,
            customer_id=customer_id,
            billing_enabled=payload.billing_enabled,
            lead_rate_cents_per_thousand=(
                payload.lead_rate_cents_per_thousand
            ),
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except BillingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    db.commit()
    return wallet_view(db, customer)


@router.post(
    "/api/admin/customers/{customer_id}/billing/adjustments",
    status_code=201,
)
def create_credit_adjustment(
    customer_id: int,
    payload: CreditAdjustmentCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        post_admin_adjustment(
            db,
            customer_id=customer_id,
            amount_cents=payload.amount_cents,
            actor_id=principal.user_id,
            reason=payload.reason,
        )
    except BillingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    customer = db.get(Customer, customer_id)
    db.commit()
    return wallet_view(db, customer)
