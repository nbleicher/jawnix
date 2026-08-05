from __future__ import annotations

import uuid

from jawnix.jobs import claim_next_job
from jawnix.models import Job, JobStatus


def test_claim_prefers_notify_over_emit_lead_assigned(session):
    """Customer-facing jobs must not wait behind a large metrics emit."""
    request_a = uuid.uuid4()
    request_b = uuid.uuid4()
    emit = Job(
        kind="emit_lead_assigned",
        request_id=request_a,
        status=JobStatus.queued.value,
    )
    notify = Job(
        kind="notify_request",
        request_id=request_b,
        status=JobStatus.queued.value,
    )
    # Emit is older (lower id) so FIFO alone would claim it first.
    session.add(emit)
    session.flush()
    session.add(notify)
    session.flush()
    assert emit.id < notify.id

    claimed = claim_next_job(session, "priority-worker")
    assert claimed is not None
    assert claimed.id == notify.id
    assert claimed.kind == "notify_request"
