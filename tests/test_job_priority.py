from __future__ import annotations

import uuid

import pytest

from jawnix.jobs import (
    WORKER_LANE_DEFAULT,
    WORKER_LANE_METRICS,
    claim_next_job,
    higher_priority_job_waiting,
)
from jawnix.models import Job, JobStatus


@pytest.mark.parametrize("lane,expected", [
    ("fulfillment", "fulfill_round_robin"),
    ("notifications", "deliver_request"),
    ("maintenance", "sync_inventory"),
    ("imports", "import_lead_upload"),
])
def test_worker_lanes_isolate_slow_notification_and_inventory_jobs(session, lane, expected):
    for kind in ("import_lead_upload", "send_request_milestone_email", "sync_inventory", "update_notification", "fulfill_round_robin", "deliver_request"):
        session.add(Job(kind=kind, payload={}))
    session.flush()
    item = claim_next_job(session, f"{lane}-worker", lane=lane)
    assert item is not None and item.kind == expected


def test_lane_mapping_covers_fulfillment_controls_and_notifications():
    from jawnix.jobs import job_lane
    assert job_lane("telegram_inventory_conflict_action") == "fulfillment"
    assert job_lane("telegram_action") == "fulfillment"
    assert job_lane("notify_nightly_review") == "notifications"
    assert job_lane("send_request_milestone_email") == "notifications"
    assert job_lane("unknown_maintenance_job") == "maintenance"


def test_claim_prefers_notify_over_emit_on_default_lane_when_both_allowed(session):
    """Default lane never claims emit; metrics lane never claims notify."""
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
    session.add(emit)
    session.flush()
    session.add(notify)
    session.flush()
    assert emit.id < notify.id

    claimed = claim_next_job(
        session, "priority-worker", lane=WORKER_LANE_DEFAULT
    )
    assert claimed is not None
    assert claimed.id == notify.id
    assert claimed.kind == "notify_request"


def test_default_lane_skips_emit_even_when_only_emit_is_queued(session):
    session.add(
        Job(
            kind="emit_lead_assigned",
            request_id=uuid.uuid4(),
            status=JobStatus.queued.value,
        )
    )
    session.flush()
    assert (
        claim_next_job(session, "default-worker", lane=WORKER_LANE_DEFAULT)
        is None
    )


def test_metrics_lane_claims_only_emit(session):
    notify = Job(
        kind="notify_request",
        request_id=uuid.uuid4(),
        status=JobStatus.queued.value,
    )
    emit = Job(
        kind="emit_lead_assigned",
        request_id=uuid.uuid4(),
        status=JobStatus.queued.value,
    )
    session.add(notify)
    session.flush()
    session.add(emit)
    session.flush()

    claimed = claim_next_job(
        session, "metrics-worker", lane=WORKER_LANE_METRICS
    )
    assert claimed is not None
    assert claimed.kind == "emit_lead_assigned"
    assert claimed.id == emit.id


def test_higher_priority_job_waiting_ignores_emit_on_default_lane(session):
    session.add(
        Job(
            kind="emit_lead_assigned",
            request_id=uuid.uuid4(),
            status=JobStatus.queued.value,
        )
    )
    session.flush()
    assert higher_priority_job_waiting(session) is False
    session.add(
        Job(
            kind="notify_request",
            request_id=uuid.uuid4(),
            status=JobStatus.queued.value,
        )
    )
    session.flush()
    assert higher_priority_job_waiting(session) is True
    assert (
        higher_priority_job_waiting(session, lane=WORKER_LANE_METRICS) is False
    )
