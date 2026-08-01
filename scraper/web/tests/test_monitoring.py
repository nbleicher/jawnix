from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.monitoring import build_trends, classify, duration, percent


SETTINGS = SimpleNamespace(
    telemetry_stale_secs=180, expected_workers=8, queue_max_depth=500,
    queue_max_age_mins=30, max_retryable=50, max_empty_rate=0.9,
    max_spool_files=200, max_spool_age_mins=15, disk_warn_percent=85,
    memory_warn_percent=90,
)


def sample(now, **overrides):
    value = {
        "captured_at": now, "database_ok": True, "dashboard_ok": True,
        "queue_api_ok": True, "required_services_ok": True, "expected_workers": 8,
        "running_workers": 8, "unhealthy_workers": 0, "queue_depth": 10,
        "running_jobs": 8, "retryable_jobs": 0, "oldest_queue_seconds": 30,
        "empty_rate_1h": 0.2, "spool_pending_files": 0, "spool_oldest_seconds": 0,
        "disk_used_bytes": 10, "disk_total_bytes": 100,
        "memory_used_bytes": 20, "memory_total_bytes": 100,
    }
    value.update(overrides)
    return value


def test_status_classification():
    now = datetime.now(timezone.utc)
    assert classify(sample(now), {"status": "ok", "messages": []}, SETTINGS, now)["key"] == "operational"
    assert classify(sample(now, queue_depth=0, running_jobs=0), {"status": "ok", "messages": []}, SETTINGS, now)["key"] == "idle"
    assert classify(sample(now, running_workers=7), {"status": "ok", "messages": []}, SETTINGS, now)["key"] == "attention"
    assert classify(sample(now), {"status": "alert", "messages": ["queue starved"]}, SETTINGS, now)["key"] == "attention"
    assert classify(sample(now-timedelta(minutes=4)), None, SETTINGS, now)["key"] == "stale"
    assert classify(None, None, SETTINGS, now)["key"] == "stale"


def test_trends_bucket_deltas_and_percent_helpers():
    now = datetime.now(timezone.utc).replace(minute=30, second=0, microsecond=0)
    rows = [
        {"captured_at": now-timedelta(hours=1), "cpu_percent": 10, "memory_used_bytes": 25,
         "memory_total_bytes": 100, "queue_depth": 5, "businesses_total": 100, "completed_jobs_total": 50},
        {"captured_at": now, "cpu_percent": 20, "memory_used_bytes": 50,
         "memory_total_bytes": 100, "queue_depth": 8, "businesses_total": 115, "completed_jobs_total": 60},
    ]
    trends = build_trends(rows, now)
    assert len(trends) == 24
    assert trends[-1]["businesses"] == 15
    assert trends[-1]["jobs"] == 10
    assert trends[-1]["queue"] == 8
    assert trends[-1]["memory"] == 50
    assert percent(1, 4) == 25
    assert duration(90061) == "1d 1h"
