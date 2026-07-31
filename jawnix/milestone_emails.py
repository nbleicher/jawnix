"""Customer email for meaningful Batch Request milestones.

The fulfillment state machine moves through more states than a Customer needs
in their inbox. This module is the allow-list: approval, Waiting for Inventory,
rejection, and failure receive a concise email; delivery remains the existing
durably retried notification job, now pointing to the portal rather than
carrying the Batch Artifact itself.

Jobs and Resend use the same request/milestone identity. The job lookup avoids
queue churn when a status is processed repeatedly, while Resend's idempotency
key closes the send/commit gap if a worker is interrupted after the provider
accepts a message.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .jobs import enqueue_job
from .models import Job, LeadRequest, RequestStatus


MILESTONE_EMAIL_JOB = "send_request_milestone_email"

_MILESTONE_BY_STATUS = {
    RequestStatus.approved.value: "approval",
    RequestStatus.waiting_inventory.value: "waiting_inventory",
    RequestStatus.rejected.value: "rejection",
    RequestStatus.failed.value: "failure",
}


@dataclass(frozen=True)
class MilestoneMessage:
    subject: str
    text: str


def customer_overview_url(settings: Settings) -> str:
    """The authenticated Customer attention queue used by email links."""

    return f"{settings.public_base_url.rstrip('/')}/app/overview"


def _request_summary(request: LeadRequest) -> str:
    states = ", ".join(sorted(request.states_snapshot))
    return (
        f"Batch Request: {request.id}\n"
        f"Quantity: {request.lead_count:,} Leads\n"
        f"Licensed States: {states}"
    )


def build_milestone_message(
    request: LeadRequest,
    milestone: str,
    settings: Settings,
) -> MilestoneMessage:
    """Customer-safe copy for one allow-listed milestone."""

    overview = customer_overview_url(settings)
    summary = _request_summary(request)
    if milestone == "approval":
        subject = f"Batch Request approved — {request.id}"
        update = (
            "Your Batch Request was approved. Jawnix is checking eligible "
            "inventory and will prepare the full Batch when it is available."
        )
    elif milestone == "waiting_inventory":
        subject = f"Batch Request waiting for inventory — {request.id}"
        update = (
            "Your approved Batch Request is waiting for enough matching "
            "inventory. Nothing has gone wrong and there is nothing you need "
            "to do; it will continue automatically when the full quantity is "
            "available."
        )
    elif milestone == "rejection":
        subject = f"Batch Request not approved — {request.id}"
        update = (
            "This Batch Request was not approved, and no Leads were reserved. "
            "You can review its timeline and submit a new request with a "
            "different quantity or scope."
        )
    elif milestone == "failure":
        subject = f"Batch Request needs attention — {request.id}"
        update = (
            "Jawnix could not finish this Batch Request. Do not submit a "
            "duplicate request; review the current timeline for the valid "
            "next action or contact Jawnix for help."
        )
    else:
        raise ValueError(f"Unknown Batch Request email milestone: {milestone}")

    return MilestoneMessage(
        subject=subject,
        text=(
            f"{update}\n\n"
            f"{summary}\n\n"
            "View anything that needs your attention after signing in:\n"
            f"{overview}\n"
        ),
    )


def enqueue_milestone_email(
    session: Session,
    request: LeadRequest,
) -> Job | None:
    """Queue the current meaningful milestone once.

    Callers already hold the Batch Request row lock while changing status.
    Looking up an existing milestone job inside that transaction therefore
    gives repeated status processing one durable queue item without adding a
    second persistence path or schema.
    """

    milestone = _MILESTONE_BY_STATUS.get(request.status)
    if milestone is None:
        return None
    existing = session.scalars(
        select(Job).where(
            Job.kind == MILESTONE_EMAIL_JOB,
            Job.request_id == request.id,
        )
    )
    for job in existing:
        if job.payload.get("milestone") == milestone:
            return job
    return enqueue_job(
        session,
        MILESTONE_EMAIL_JOB,
        request.id,
        payload={"milestone": milestone},
    )


def send_milestone_email(
    request: LeadRequest,
    milestone: str,
    settings: Settings,
) -> str:
    """Send one milestone through the existing Resend provider."""

    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is not configured.")
    message = build_milestone_message(request, milestone, settings)
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": (
                f"jawnix-request/{request.id}/{milestone}"
            ),
        },
        json={
            "from": settings.batch_from_email,
            "to": [request.delivery_email],
            "subject": message.subject,
            "text": message.text,
        },
        timeout=60,
    )
    if response.status_code >= 300:
        raise RuntimeError(
            "Resend failed to send Batch Request milestone "
            f"with HTTP {response.status_code}"
        )
    return str(response.json().get("id") or "")
