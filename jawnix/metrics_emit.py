"""Emit lead.assigned events to the Summit metrics ingest endpoint.

One logical emit per Batch Request (enqueued beside deliver_request) POSTs one
event per DistributionEvent. Work is split into small chunks so the single
worker can interleave Telegram / email jobs between chunks instead of blocking
on tens of thousands of sequential HTTP calls.

At-least-once delivery is fine: metrics dedups on distribution_event.id.
lead.opened / lead.accepted do not exist here.

Design note (accepted): the POSTs run inside the worker's job transaction.
If the transaction rolls back after some POSTs succeeded, the retry re-POSTs
the same dedup keys and metrics discards the duplicates, so no isolation of
network I/O from the transaction is needed. Chunk continuations also re-POST
from ``after_id``; metrics dedup makes overlapping edges safe.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import DistributionEvent

log = logging.getLogger("jawnix.metrics_emit")

EMIT_LEAD_ASSIGNED_JOB = "emit_lead_assigned"

# Cap how long one worker claim spends on metrics so notify / email jobs can
# run between chunks. With concurrent posts, 100 events finishes quickly while
# still yielding the worker between chunks for Telegram/delivery work.
EMIT_CHUNK_SIZE = 100
# Parallel POSTs inside one chunk. Metrics dedups on distribution_event.id, so
# ordering within a chunk does not matter.
EMIT_CONCURRENCY = 8

# Metrics dedups on distribution_event.id, so at-least-once retries are safe.
# Be generous before giving up; a failed job needs a manual requeue.
EMIT_MAX_ATTEMPTS = 10
_RETRY_DELAYS_SECONDS = (60, 300, 900, 1800, 3600)

# Warn once per worker process (not per job) when the emitter is unconfigured,
# so a misconfigured prod deploy is visible in logs without spamming them.
_unconfigured_warned = False


class MetricsEmitTransientError(RuntimeError):
    """Network failure or 5xx from metrics ingest; the job should be retried."""


@dataclass(frozen=True)
class EmitLeadAssignedResult:
    """One chunk of emit work.

    ``next_after_id`` is set when more DistributionEvents remain; the worker
    enqueues another ``emit_lead_assigned`` job with that cursor.
    """

    posted: int
    next_after_id: int | None


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
    *,
    after_id: int | None = None,
    limit: int | None = None,
) -> EmitLeadAssignedResult:
    """POST lead.assigned for up to ``limit`` DistributionEvents on the request.

    Starts after ``after_id`` when continuing a prior chunk. Returns how many
    events were posted and the cursor for the next chunk (or None when done).

    When metrics ingest is not configured, returns posted=0 without error so
    undeployed hosts do not fail the job.
    """
    chunk_limit = EMIT_CHUNK_SIZE if limit is None else limit
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
        return EmitLeadAssignedResult(posted=0, next_after_id=None)

    if chunk_limit < 1:
        raise ValueError("emit_lead_assigned limit must be >= 1")

    query = (
        select(DistributionEvent)
        .where(DistributionEvent.request_id == request_id)
        .order_by(DistributionEvent.id)
    )
    if after_id is not None:
        query = query.where(DistributionEvent.id > after_id)
    # Fetch one extra row so we know whether another chunk is needed without
    # a separate COUNT query on large request batches.
    events = list(session.scalars(query.limit(chunk_limit + 1)))
    if not events and after_id is None:
        raise LookupError(
            f"No DistributionEvents for request {request_id}; cannot emit lead.assigned."
        )
    if not events:
        return EmitLeadAssignedResult(posted=0, next_after_id=None)

    chunk = events[:chunk_limit]
    has_more = len(events) > chunk_limit

    headers = {
        "X-Ingest-Secret": settings.metrics_ingest_secret,
        "Content-Type": "application/json",
    }
    workers = max(1, min(EMIT_CONCURRENCY, len(chunk)))
    posted = 0
    with httpx.Client(timeout=30) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _post_lead_assigned,
                    client,
                    settings.metrics_ingest_url,
                    headers,
                    event,
                )
                for event in chunk
            ]
            for future in as_completed(futures):
                future.result()
                posted += 1
    next_after_id = chunk[-1].id if has_more else None
    return EmitLeadAssignedResult(posted=posted, next_after_id=next_after_id)


def _post_lead_assigned(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    event: DistributionEvent,
) -> None:
    """POST one lead.assigned event; raise transient/permanent errors."""
    body = lead_assigned_body(event)
    try:
        response = client.post(url, headers=headers, json=body)
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
        return
    if response.status_code == 422 and _is_held_response(response):
        return
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
