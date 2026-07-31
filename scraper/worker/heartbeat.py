#!/usr/bin/env python3
"""Collect worker, host, container, service, and pipeline telemetry."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import Json


BOX_ID = os.environ.get("BOX_ID", "box1")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
API_KEY = os.environ.get("API_KEY", "")
QUEUE_API_URL = os.environ.get("QUEUE_API_URL", "http://127.0.0.1:8080/api/v1/jobs")
SPOOL_DIR = Path(os.environ.get("GMS_SPOOL_DIR", "/data/incoming"))
EXPECTED_WORKERS = int(os.environ.get("EXPECTED_WORKERS", os.environ.get("WORKER_REPLICAS", "0")))
ZERO_THROUGHPUT_GRACE_SECONDS = int(
    os.environ.get("ZERO_THROUGHPUT_GRACE_SECONDS", "300")
)

REQUIRED_UNITS = (
    "docker.service",
    "gms-serve.service",
    "gms-enqueue.service",
    "gms-keyword-rollover.timer",
    "gms-heartbeat.timer",
    "gms-ship.path",
    "gms-ship.timer",
    "gms-alert.timer",
    "gms-export.timer",
    "enqueue-trigger.path",
    "gms-uptime.timer",
)


def run(command: list[str], timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def docker_service_containers(service: str) -> list[str]:
    result = run([
        "docker", "ps", "-a",
        "--filter", f"label=com.docker.compose.service={service}",
        "--format", "{{.Names}}",
    ])
    if result.returncode != 0:
        return []
    return [name.strip() for name in result.stdout.splitlines() if name.strip()]


def inspect_container(container: str) -> dict:
    result = run(["docker", "inspect", container])
    if result.returncode != 0:
        return {"running": False, "healthy": False, "restarts": 0, "status": "missing"}
    try:
        inspected = json.loads(result.stdout)[0]
        state = inspected["State"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"running": False, "healthy": False, "restarts": 0, "status": "unknown"}
    running = bool(state.get("Running"))
    health = state.get("Health", {}).get("Status")
    status = health or state.get("Status", "unknown")
    return {
        "container_id": str(inspected.get("Id", ""))[:12],
        "running": running,
        "healthy": running and health != "unhealthy",
        "restarts": int(inspected.get("RestartCount", 0)),
        "status": status,
        "started_at": str(state.get("StartedAt", "")),
    }


def worker_health(container: str, container_state: dict) -> dict:
    if not container_state["running"]:
        return {"active_jobs": 0, "jobs_processed": 0, "results_per_minute": 0.0,
                "status": container_state["status"]}
    result = run(["docker", "exec", container, "curl", "-sf", "localhost:8080/health"])
    if result.returncode != 0:
        return {"active_jobs": 0, "jobs_processed": 0, "results_per_minute": 0.0,
                "status": "unreachable"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"active_jobs": 0, "jobs_processed": 0, "results_per_minute": 0.0,
                "status": "invalid"}


def systemd_states() -> dict[str, dict[str, str]]:
    states = {}
    for unit in REQUIRED_UNITS:
        result = run([
            "systemctl", "show", unit,
            "--property=LoadState", "--property=ActiveState", "--property=SubState",
        ])
        values = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        states[unit] = {
            "load": values.get("LoadState", "unknown"),
            "active": values.get("ActiveState", "unknown"),
            "sub": values.get("SubState", "unknown"),
        }
    enabled = bool(os.environ.get("UPTIME_HEARTBEAT_URL", "").strip())
    states["external_heartbeat"] = {
        "load": "configured" if enabled else "not-configured",
        "active": "active" if enabled else "disabled",
        "sub": "configured" if enabled else "not configured",
    }
    return states


def read_cpu_times(path: Path = Path("/proc/stat")) -> tuple[int, int]:
    fields = path.read_text().splitlines()[0].split()[1:]
    values = [int(value) for value in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def cpu_percent(sample_seconds: float = 0.2) -> float:
    total_before, idle_before = read_cpu_times()
    time.sleep(sample_seconds)
    total_after, idle_after = read_cpu_times()
    elapsed = total_after - total_before
    if elapsed <= 0:
        return 0.0
    busy = elapsed - (idle_after - idle_before)
    return round(max(0.0, min(100.0, busy * 100.0 / elapsed)), 1)


def memory_bytes(path: Path = Path("/proc/meminfo")) -> tuple[int, int]:
    values = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            values[key] = int(value.strip().split()[0]) * 1024
    total = values.get("MemTotal", 0)
    return total - values.get("MemAvailable", 0), total


def spool_metrics(path: Path = SPOOL_DIR) -> tuple[int, int]:
    if not path.is_dir():
        return 0, 0
    mtimes = [entry.stat().st_mtime for entry in path.iterdir()
              if entry.is_file() and entry.name.endswith(".ndjson.done")]
    if not mtimes:
        return 0, 0
    return len(mtimes), int(max(0, time.time() - min(mtimes)))


def queue_api_ok() -> bool:
    request = Request(QUEUE_API_URL)
    if API_KEY:
        request.add_header("X-API-Key", API_KEY)
    try:
        with urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def container_service_health(service: str) -> bool:
    containers = docker_service_containers(service)
    return bool(containers) and all(inspect_container(name)["healthy"] for name in containers)


def pipeline_metrics(conn) -> dict:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              count(*) FILTER (WHERE state IN ('available','pending','scheduled','retryable')),
              count(*) FILTER (WHERE state='running'),
              count(*) FILTER (WHERE state='retryable'),
              COALESCE(extract(epoch FROM now()-min(created_at) FILTER
                (WHERE state IN ('available','pending','retryable'))), 0)::bigint,
              (SELECT count(*) FROM businesses),
              (SELECT count(*) FROM scrape_results),
              COALESCE((SELECT avg((result_count=0)::int)::float
                FROM scrape_results WHERE created_at >= now()-interval '1 hour'), 0)
            FROM river_job WHERE kind='scrape'
            """
        )
        row = cursor.fetchone()
    return {
        "queue_depth": int(row[0]),
        "running_jobs": int(row[1]),
        "retryable_jobs": int(row[2]),
        "oldest_queue_seconds": int(row[3]),
        "businesses_total": int(row[4]),
        "completed_jobs_total": int(row[5]),
        "empty_rate_1h": float(row[6]),
    }


def upsert_worker(cursor, container: str, container_id: str, health: dict) -> None:
    cursor.execute(
        """
        INSERT INTO worker_heartbeats
            (box_id, container_name, container_id, reported_at, active_jobs,
             jobs_processed, results_per_min, status)
        VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s)
        ON CONFLICT (box_id, container_name) DO UPDATE SET
            container_id=EXCLUDED.container_id, reported_at=EXCLUDED.reported_at,
            active_jobs=EXCLUDED.active_jobs,
            jobs_processed=EXCLUDED.jobs_processed, results_per_min=EXCLUDED.results_per_min,
            status=EXCLUDED.status
        """,
        (BOX_ID, container, container_id, int(health.get("active_jobs", 0)),
         int(health.get("jobs_processed", 0)),
         float(health.get("results_per_minute", 0.0)), health.get("status", "unknown")),
    )


def container_age_seconds(
    started_at: str,
    now: datetime | None = None,
) -> float:
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    current = now or datetime.now(timezone.utc)
    return max(0, (current - started).total_seconds())


def worker_is_unhealthy(
    health: dict,
    queue_depth: int,
    started_at: str = "",
    zero_throughput_grace_seconds: int = ZERO_THROUGHPUT_GRACE_SECONDS,
) -> bool:
    if health.get("status") not in {"ok", "alive"}:
        return True
    if queue_depth <= 0:
        return False
    if int(health.get("active_jobs", 0)) == 0:
        return True
    return (
        float(health.get("results_per_minute", 0.0)) <= 0
        and container_age_seconds(started_at)
        >= zero_throughput_grace_seconds
    )


def collect_and_store(conn) -> dict:
    metrics = pipeline_metrics(conn)
    workers = docker_service_containers("worker")
    worker_rows = []
    restart_count = 0
    running_workers = 0
    unhealthy_workers = 0
    for container in workers:
        state = inspect_container(container)
        health = worker_health(container, state)
        restart_count += state["restarts"]
        running_workers += int(state["running"])
        unhealthy_workers += int(
            worker_is_unhealthy(
                health,
                metrics["queue_depth"],
                state.get("started_at", ""),
            )
        )
        worker_rows.append((container, state.get("container_id", ""), health))

    services = systemd_states()
    required_services_ok = all(
        services[unit]["load"] == "loaded" and services[unit]["active"] == "active"
        for unit in REQUIRED_UNITS
    )
    disk = shutil.disk_usage("/")
    memory_used, memory_total = memory_bytes()
    spool_pending, spool_oldest = spool_metrics()
    with conn.cursor() as cursor:
        for container, container_id, health in worker_rows:
            upsert_worker(cursor, container, container_id, health)
        if workers:
            cursor.execute(
                "DELETE FROM worker_heartbeats WHERE box_id=%s AND NOT (container_name=ANY(%s))",
                (BOX_ID, workers),
            )
        else:
            cursor.execute("DELETE FROM worker_heartbeats WHERE box_id=%s", (BOX_ID,))

        cursor.execute(
            """
            INSERT INTO stack_samples (
              box_id, captured_at, host_uptime_seconds, cpu_percent, load_1,
              memory_used_bytes, memory_total_bytes, disk_used_bytes, disk_total_bytes,
              spool_pending_files, spool_oldest_seconds, expected_workers, running_workers,
              unhealthy_workers, worker_restarts, database_ok, dashboard_ok, queue_api_ok,
              required_services_ok, services, queue_depth, running_jobs, retryable_jobs,
              oldest_queue_seconds, businesses_total, completed_jobs_total, empty_rate_1h
            ) VALUES (
              %s, date_trunc('minute', NOW()), %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (box_id, captured_at) DO UPDATE SET
              host_uptime_seconds=EXCLUDED.host_uptime_seconds,
              cpu_percent=EXCLUDED.cpu_percent, load_1=EXCLUDED.load_1,
              memory_used_bytes=EXCLUDED.memory_used_bytes,
              memory_total_bytes=EXCLUDED.memory_total_bytes,
              disk_used_bytes=EXCLUDED.disk_used_bytes, disk_total_bytes=EXCLUDED.disk_total_bytes,
              spool_pending_files=EXCLUDED.spool_pending_files,
              spool_oldest_seconds=EXCLUDED.spool_oldest_seconds,
              expected_workers=EXCLUDED.expected_workers, running_workers=EXCLUDED.running_workers,
              unhealthy_workers=EXCLUDED.unhealthy_workers, worker_restarts=EXCLUDED.worker_restarts,
              database_ok=EXCLUDED.database_ok, dashboard_ok=EXCLUDED.dashboard_ok,
              queue_api_ok=EXCLUDED.queue_api_ok,
              required_services_ok=EXCLUDED.required_services_ok, services=EXCLUDED.services,
              queue_depth=EXCLUDED.queue_depth, running_jobs=EXCLUDED.running_jobs,
              retryable_jobs=EXCLUDED.retryable_jobs,
              oldest_queue_seconds=EXCLUDED.oldest_queue_seconds,
              businesses_total=EXCLUDED.businesses_total,
              completed_jobs_total=EXCLUDED.completed_jobs_total,
              empty_rate_1h=EXCLUDED.empty_rate_1h
            """,
            (BOX_ID, int(float(Path("/proc/uptime").read_text().split()[0])), cpu_percent(),
             os.getloadavg()[0], memory_used, memory_total, disk.used, disk.total,
             spool_pending, spool_oldest, EXPECTED_WORKERS, running_workers, unhealthy_workers,
             restart_count, container_service_health("dashboard"), queue_api_ok(),
             required_services_ok, Json(services), metrics["queue_depth"], metrics["running_jobs"],
             metrics["retryable_jobs"], metrics["oldest_queue_seconds"],
             metrics["businesses_total"], metrics["completed_jobs_total"], metrics["empty_rate_1h"]),
        )
        cursor.execute("DELETE FROM stack_samples WHERE captured_at < NOW()-interval '30 days'")
        cursor.execute("DELETE FROM pipeline_alert_events WHERE checked_at < NOW()-interval '30 days'")
    conn.commit()
    return {
        "workers": len(workers), "running": running_workers, "unhealthy": unhealthy_workers,
        **metrics,
    }


def main() -> int:
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            summary = collect_and_store(conn)
    except Exception as error:
        print(f"ERROR: telemetry collection failed: {error}", file=sys.stderr)
        return 1
    print(
        f"telemetry: {summary['running']}/{EXPECTED_WORKERS} workers running, "
        f"{summary['queue_depth']} queued, {summary['unhealthy']} unhealthy -> {BOX_ID}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
