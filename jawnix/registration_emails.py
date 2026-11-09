"""Tell the person who signed up when the operator decides their registration.

A verified signup whose name matches an archived Customer waits on the
"Account awaiting review" page until the operator restores that Customer,
creates a separate one, or rejects access. Without an email the person only
learns the outcome by coming back and pressing "Check status".

The decision queues one job in the same transaction that records it, and the
job sends through the existing Resend provider. The job identity and Resend's
idempotency key are both the registration and its outcome, so a repeated job
or an interrupted worker never sends twice.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .jobs import enqueue_job
from .models import CustomerRegistration, Job
from .states import US_STATE_OPTIONS


REGISTRATION_EMAIL_JOB = "send_registration_decision_email"

_STATE_NAMES = dict(US_STATE_OPTIONS)


@dataclass(frozen=True)
class RegistrationMessage:
    subject: str
    text: str


def _appointed_states(registration: CustomerRegistration) -> str:
    return ", ".join(
        _STATE_NAMES.get(code, code) for code in sorted(registration.licensed_states)
    )


def build_registration_message(
    registration: CustomerRegistration,
    settings: Settings,
    *,
    restored: bool = False,
) -> RegistrationMessage:
    """Customer-safe copy for an approved or rejected registration."""

    greeting = f"Hi {registration.first_name},\n\n" if registration.first_name else ""
    signature = "— Noah\nJAWNIX · noah@jawnix.com\n"
    if registration.status == "active":
        history = (
            "Your earlier batches and billing history are in your account.\n\n"
            if restored
            else ""
        )
        return RegistrationMessage(
            subject="Your Jawnix account is ready",
            text=(
                f"{greeting}"
                "I reviewed your registration and your account is ready. "
                "You can sign in and request leads now.\n\n"
                f"{history}"
                f"Appointed states: {_appointed_states(registration)}\n\n"
                "Sign in:\n"
                f"{settings.public_base_url.rstrip('/')}/app/home\n\n"
                "Questions? Reply to this email — it comes straight to me.\n\n"
                f"{signature}"
            ),
        )
    if registration.status == "rejected":
        return RegistrationMessage(
            subject="Your Jawnix registration",
            text=(
                f"{greeting}"
                "I reviewed your registration and could not approve access "
                "to Jawnix.\n\n"
                "If you think this is a mistake, reply to this email — it "
                "comes straight to me.\n\n"
                f"{signature}"
            ),
        )
    raise ValueError("Only a decided registration has an email.")


def enqueue_registration_email(
    session: Session,
    registration: CustomerRegistration,
    *,
    restored: bool = False,
) -> Job:
    """Queue the decision email once per registration and outcome."""

    user_id = str(registration.auth_user_id)
    for job in session.scalars(
        select(Job).where(Job.kind == REGISTRATION_EMAIL_JOB)
    ):
        if (
            job.payload.get("user_id") == user_id
            and job.payload.get("status") == registration.status
        ):
            return job
    return enqueue_job(
        session,
        REGISTRATION_EMAIL_JOB,
        payload={
            "user_id": user_id,
            "status": registration.status,
            "restored": restored,
        },
    )


def send_registration_email(
    registration: CustomerRegistration,
    settings: Settings,
    *,
    restored: bool = False,
) -> str:
    """Send the decision email through the existing Resend provider."""

    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is not configured.")
    message = build_registration_message(registration, settings, restored=restored)
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": (
                f"jawnix-registration/{registration.auth_user_id}/"
                f"{registration.status}"
            ),
        },
        json={
            "from": settings.batch_from_email,
            "to": [registration.email],
            "subject": message.subject,
            "text": message.text,
        },
        timeout=60,
    )
    if response.status_code >= 300:
        raise RuntimeError(
            "Resend failed to send the registration decision "
            f"with HTTP {response.status_code}"
        )
    return str(response.json().get("id") or "")
