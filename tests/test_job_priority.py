from __future__ import annotations

import pytest

from jawnix.jobs import claim_next_job
from jawnix.models import Job


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
