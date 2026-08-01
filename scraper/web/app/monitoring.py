from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


SERVICE_LABELS = (
    ("postgres", "PostgreSQL"),
    ("dashboard", "Dashboard"),
    ("queue_endpoint", "Queue endpoint"),
    ("docker.service", "Docker engine"),
    ("gms-serve.service", "Queue API service"),
    ("gms-enqueue.service", "Enqueuer"),
    ("gms-keyword-rollover.timer", "Keyword rollover timer"),
    ("gms-heartbeat.timer", "Telemetry timer"),
    ("gms-ship.path", "Result shipper watch"),
    ("gms-ship.timer", "Result shipper backstop"),
    ("gms-alert.timer", "Pipeline alert timer"),
    ("gms-export.timer", "Daily export timer"),
    ("enqueue-trigger.path", "Dashboard enqueue trigger"),
    ("gms-uptime.timer", "External uptime timer"),
    ("external_heartbeat", "Better Stack heartbeat"),
)


def percent(used: int | float, total: int | float) -> float:
    return round((float(used) * 100 / float(total)), 1) if total else 0.0


def duration(seconds: int | float) -> str:
    value = max(0, int(seconds))
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m"
    if value < 86400:
        return f"{value // 3600}h {value % 3600 // 60}m"
    return f"{value // 86400}d {value % 86400 // 3600}h"


def service_rows(sample: dict[str, Any] | None) -> list[dict[str, str]]:
    if not sample:
        return []
    raw = sample.get("services") or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    synthetic = {
        "postgres": (bool(sample.get("database_ok")), "healthy" if sample.get("database_ok") else "unhealthy"),
        "dashboard": (bool(sample.get("dashboard_ok")), "healthy" if sample.get("dashboard_ok") else "unhealthy"),
        "queue_endpoint": (bool(sample.get("queue_api_ok")), "responding" if sample.get("queue_api_ok") else "unreachable"),
    }
    rows = []
    for key, label in SERVICE_LABELS:
        if key in synthetic:
            healthy, detail = synthetic[key]
        else:
            state = raw.get(key, {})
            active = state.get("active", "unknown")
            healthy = active == "active"
            detail = state.get("sub") or active
            if key == "external_heartbeat" and active == "disabled":
                detail = "not configured"
        rows.append({
            "key": key,
            "label": label,
            "state": "ok" if healthy else ("neutral" if detail == "not configured" else "bad"),
            "detail": detail,
        })
    return rows


def classify(sample: dict[str, Any] | None, latest_alert: dict[str, Any] | None,
             settings, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if not sample:
        return {"key": "stale", "label": "Telemetry unavailable", "detail": "No host sample received", "reasons": []}
    captured_at = sample["captured_at"]
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    age = max(0, int((now - captured_at).total_seconds()))
    if age > settings.telemetry_stale_secs:
        return {
            "key": "stale", "label": "Telemetry stale",
            "detail": f"Last host sample {duration(age)} ago", "reasons": [], "age_seconds": age,
        }

    reasons = []
    if not sample.get("database_ok"):
        reasons.append("PostgreSQL is unhealthy")
    if not sample.get("dashboard_ok"):
        reasons.append("Dashboard container is unhealthy")
    if not sample.get("queue_api_ok"):
        reasons.append("Queue API is unreachable")
    if not sample.get("required_services_ok"):
        reasons.append("A required host service is inactive")

    expected = int(sample.get("expected_workers") or settings.expected_workers)
    running = int(sample.get("running_workers") or 0)
    unhealthy = int(sample.get("unhealthy_workers") or 0)
    if running < expected:
        reasons.append(f"Only {running} of {expected} workers are running")
    if unhealthy:
        reasons.append(f"{unhealthy} worker{'s are' if unhealthy != 1 else ' is'} unhealthy")
    if int(sample.get("queue_depth") or 0) > settings.queue_max_depth:
        reasons.append("Queue depth exceeds its warning threshold")
    if int(sample.get("oldest_queue_seconds") or 0) > settings.queue_max_age_mins * 60:
        reasons.append("Oldest queued job exceeds its warning threshold")
    if int(sample.get("retryable_jobs") or 0) > settings.max_retryable:
        reasons.append("Retryable jobs exceed their warning threshold")
    if float(sample.get("empty_rate_1h") or 0) > settings.max_empty_rate:
        reasons.append("Empty-result rate exceeds its warning threshold")
    if int(sample.get("spool_pending_files") or 0) > settings.max_spool_files:
        reasons.append("Result spool file count exceeds its warning threshold")
    if int(sample.get("spool_oldest_seconds") or 0) > settings.max_spool_age_mins * 60:
        reasons.append("Oldest result spool file exceeds its warning threshold")
    if percent(sample.get("disk_used_bytes", 0), sample.get("disk_total_bytes", 0)) > settings.disk_warn_percent:
        reasons.append("Disk usage exceeds its warning threshold")
    if percent(sample.get("memory_used_bytes", 0), sample.get("memory_total_bytes", 0)) > settings.memory_warn_percent:
        reasons.append("Memory usage exceeds its warning threshold")
    if latest_alert and latest_alert.get("status") != "ok":
        messages = latest_alert.get("messages") or []
        if isinstance(messages, str):
            messages = json.loads(messages)
        reasons.extend(str(message) for message in messages)
    reasons = list(dict.fromkeys(reasons))

    if reasons:
        return {
            "key": "attention", "label": "Attention needed", "detail": reasons[0],
            "reasons": reasons, "age_seconds": age,
        }
    if int(sample.get("queue_depth") or 0) + int(sample.get("running_jobs") or 0) == 0:
        return {
            "key": "idle", "label": "Healthy but idle", "detail": "No queued or running jobs",
            "reasons": [], "age_seconds": age,
        }
    return {
        "key": "operational", "label": "Operational", "detail": f"Telemetry received {duration(age)} ago",
        "reasons": [], "age_seconds": age,
    }


def build_trends(rows: Iterable[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    start = current_hour - timedelta(hours=23)
    buckets = {
        start + timedelta(hours=index): {
            "businesses": 0, "jobs": 0, "queue": 0, "cpu_sum": 0.0,
            "memory_sum": 0.0, "samples": 0,
        }
        for index in range(24)
    }
    previous = None
    for raw in rows:
        row = dict(raw)
        captured = row["captured_at"]
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        captured = captured.astimezone(timezone.utc)
        hour = captured.replace(minute=0, second=0, microsecond=0)
        if hour in buckets:
            bucket = buckets[hour]
            bucket["queue"] = int(row.get("queue_depth") or 0)
            bucket["cpu_sum"] += float(row.get("cpu_percent") or 0)
            bucket["memory_sum"] += percent(row.get("memory_used_bytes", 0), row.get("memory_total_bytes", 0))
            bucket["samples"] += 1
            if previous:
                bucket["businesses"] += max(0, int(row.get("businesses_total") or 0) - int(previous.get("businesses_total") or 0))
                bucket["jobs"] += max(0, int(row.get("completed_jobs_total") or 0) - int(previous.get("completed_jobs_total") or 0))
        previous = row

    maximums = {
        "businesses": max((bucket["businesses"] for bucket in buckets.values()), default=0) or 1,
        "jobs": max((bucket["jobs"] for bucket in buckets.values()), default=0) or 1,
        "queue": max((bucket["queue"] for bucket in buckets.values()), default=0) or 1,
    }
    output = []
    for hour, bucket in buckets.items():
        samples = bucket.pop("samples")
        cpu = round(bucket.pop("cpu_sum") / samples, 1) if samples else 0
        memory = round(bucket.pop("memory_sum") / samples, 1) if samples else 0
        output.append({
            "label": hour.strftime("%H:00"), **bucket, "cpu": cpu, "memory": memory,
            "businesses_height": round(bucket["businesses"] * 100 / maximums["businesses"], 1),
            "jobs_height": round(bucket["jobs"] * 100 / maximums["jobs"], 1),
            "queue_height": round(bucket["queue"] * 100 / maximums["queue"], 1),
        })
    return output
