"""Emit lead.assigned events to the Summit metrics ingest endpoint.

One job per Batch Request (enqueued beside deliver_request) POSTs one event per
DistributionEvent. At-least-once delivery is fine: metrics dedups on
distribution_event.id. lead.opened / lead.accepted do not exist here.

Design note (accepted): the POSTs run inside the worker's job transaction.
If the transaction rolls back after some POSTs succeeded, the retry re-POSTs
the same dedup keys and metrics discards the duplicates, so no isolation of
network I/O from the transaction is needed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import DistributionEvent

log = logging.getLogger("jawnix.metrics_emit")

EMIT_LEAD_ASSIGNED_JOB = "emit_lead_assigned"

# Metrics dedups on distribution_event.id, so at-least-once retries are safe.
# Be generous before giving up; a failed job needs a manual requeue.
EMIT_MAX_ATTEMPTS = 10
_RETRY_DELAYS_SECONDS = (60, 300, 900, 1800, 3600)

# Warn once per worker process (not per job) when the emitter is unconfigured,
# so a misconfigured prod deploy is visible in logs without spamming them.
_unconfigured_warned = False


class MetricsEmitTransientError(RuntimeError):
    """Network failure or 5xx from metrics ingest; the job should be retried."""


def emit_retry_delay(attempts: int) -> timedelta:
    """Backoff before the next try after `attempts` tries: 1m, 5m, 15m, 30m, 1h capped."""
    index = min(max(attempts - 1, 0), len(_RETRY_DELAYS_SECONDS) - 1)
    return timedelta(seconds=_RETRY_DELAYS_SECONDS[index])


def _is_held_response(response: httpx.Response) -> bool:
    """True when a 422 body is the backend's IngestHeld shape.

    The backend persists a 422 only for the held/unmapped-agent case, whose
    JSON body carries status="held". Every other 422 is a rejection whose
    event was not stored.
    """
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "held"


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
        global _unconfigured_warned
        if not _unconfigured_warned:
            _unconfigured_warned = True
            log.warning(
                "JAWNIX_METRICS_INGEST_URL / JAWNIX_METRICS_INGEST_SECRET are "
                "not set; emit_lead_assigned jobs no-op and lead.assigned "
                "events are silently dropped until the emitter is configured."
            )
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
        try:
            response = httpx.post(
                settings.metrics_ingest_url,
                headers={
                    "X-Ingest-Secret": settings.metrics_ingest_secret,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise MetricsEmitTransientError(
                f"Metrics ingest unreachable for distribution_event "
                f"{event.id}: {exc!r}"
            ) from exc
        # 201 accepted, 200 duplicate, 422 with a held body (unmapped agent,
        # persisted for replay) — all mean metrics stored the event. Any other
        # 4xx means the event was rejected and retrying the same body cannot
        # help, so fail loudly. 5xx / network errors are retried.
        if response.status_code in {200, 201}:
            posted += 1
            continue
        if response.status_code == 422 and _is_held_response(response):
            posted += 1
            continue
        detail = (response.text or "")[:500]
        if response.status_code >= 500:
            raise MetricsEmitTransientError(
                f"Metrics ingest failed with HTTP {response.status_code} "
                f"for distribution_event {event.id}: {detail}"
            )
        raise RuntimeError(
            f"Metrics ingest rejected distribution_event {event.id} "
            f"with HTTP {response.status_code}: {detail}"
        )
    return posted
