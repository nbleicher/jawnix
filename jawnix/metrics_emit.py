"""Emit lead.assigned events to the Summit metrics ingest endpoint.

One job per Batch Request (enqueued beside deliver_request) POSTs one event per
DistributionEvent. At-least-once delivery is fine: metrics dedups on
distribution_event.id. lead.opened / lead.accepted do not exist here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import DistributionEvent

log = logging.getLogger("jawnix.metrics_emit")

EMIT_LEAD_ASSIGNED_JOB = "emit_lead_assigned"


def _as_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _lead_source(event: DistributionEvent) -> str:
    provenance = event.listing_provenance or {}
    source = provenance.get("source")
    if isinstance(source, str) and source.strip():
        return source.strip()
    return event.source_kind or ""


def lead_assigned_body(event: DistributionEvent) -> dict:
    """Build the /ingest/jawnix body for one DistributionEvent."""
    agent = str(event.customer_id) if event.customer_id is not None else ""
    dedup_key = str(event.id)
    return {
        "dedup_key": dedup_key,
        "type": "lead.assigned",
        "actor": "system",
        "occurred_at": _as_utc_iso(event.delivered_at),
        "source_agent_identity": agent,
        "contact_ref": {
            "source_link": f"jawnix:{dedup_key}",
            "phone": event.phone,
        },
        "payload": {
            "agent": agent,
            "phone": event.phone,
            "state": event.state,
            "source": _lead_source(event),
        },
    }


def emit_lead_assigned(
    session: Session,
    request_id: uuid.UUID,
    settings: Settings,
) -> int:
    """POST lead.assigned for every DistributionEvent on the request.

    Returns the number of events posted. When metrics ingest is not configured,
    returns 0 without error so undeployed hosts do not fail the job.
    """
    if not settings.metrics_ingest_url or not settings.metrics_ingest_secret:
        log.info(
            "Metrics ingest not configured; skipping emit_lead_assigned for %s",
            request_id,
        )
        return 0

    events = list(
        session.scalars(
            select(DistributionEvent)
            .where(DistributionEvent.request_id == request_id)
            .order_by(DistributionEvent.id)
        )
    )
    if not events:
        raise LookupError(
            f"No DistributionEvents for request {request_id}; cannot emit lead.assigned."
        )

    posted = 0
    for event in events:
        body = lead_assigned_body(event)
        response = httpx.post(
            settings.metrics_ingest_url,
            headers={
                "X-Ingest-Secret": settings.metrics_ingest_secret,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        # 201 accepted, 200 duplicate, 422 held (unmapped agent) — all mean
        # metrics received the event. Everything else retries at-least-once.
        if response.status_code not in {200, 201, 422}:
            raise RuntimeError(
                f"Metrics ingest failed with HTTP {response.status_code} "
                f"for distribution_event {event.id}"
            )
        posted += 1
    return posted
