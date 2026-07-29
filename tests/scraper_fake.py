"""A controlled Scraper fake that answers the real projection contract.

Every payload here is shaped exactly like the upstream dashboard's own context
for that region — same keys, same derived fields — so a test passing against
this fake is evidence about the real contract rather than about the fake. The
per-region split mirrors ``/api/dashboard/{region}``.
"""

from __future__ import annotations

import json

import httpx


CAPTURED_AT = "2026-07-28T11:59:30+00:00"

STACK_STATUS = {
    "key": "attention",
    "label": "Attention needed",
    "detail": "Only 6 of 8 workers are running",
    "reasons": [
        "Only 6 of 8 workers are running",
        "Queue depth exceeds its warning threshold",
    ],
    "age_seconds": 30,
}

SAMPLE = {
    "captured_at": CAPTURED_AT,
    "cpu_percent": 42.5,
    "load_1": 3.2,
    "memory_used_bytes": 12_884_901_888,
    "memory_total_bytes": 17_179_869_184,
    "memory_percent": 75.0,
    "disk_used_bytes": 429_496_729_600,
    "disk_total_bytes": 536_870_912_000,
    "disk_percent": 80.0,
    "host_uptime_seconds": 950_400,
    "uptime_label": "11d 0h",
    "spool_pending_files": 12,
    "spool_oldest_seconds": 90,
    "spool_age_label": "1m",
    "worker_restarts": 2,
    "expected_workers": 8,
    "running_workers": 6,
    "unhealthy_workers": 1,
    "database_ok": True,
    "dashboard_ok": True,
    "queue_api_ok": True,
    "required_services_ok": False,
    "queue_depth": 812,
    "running_jobs": 6,
    "retryable_jobs": 4,
    "oldest_queue_seconds": 1200,
    "businesses_total": 9_244_326,
    "completed_jobs_total": 1_820_004,
    "empty_rate_1h": 0.18,
    # Upstream carries the raw systemd blob here; Jawnix must not forward it.
    "services": {"docker.service": {"active": "active", "sub": "running"}},
}

SERVICES = [
    {"key": "postgres", "label": "PostgreSQL", "state": "ok", "detail": "healthy"},
    {"key": "dashboard", "label": "Dashboard", "state": "ok", "detail": "healthy"},
    {
        "key": "queue_endpoint",
        "label": "Queue endpoint",
        "state": "ok",
        "detail": "responding",
    },
    {
        "key": "docker.service",
        "label": "Docker engine",
        "state": "ok",
        "detail": "running",
    },
    {
        "key": "gms-enqueue.service",
        "label": "Enqueuer",
        "state": "bad",
        "detail": "failed",
    },
    {
        "key": "external_heartbeat",
        "label": "Better Stack heartbeat",
        "state": "neutral",
        "detail": "not configured",
    },
]

STATS = {
    "businesses": 9_244_326,
    "phone_businesses": 4_588_286,
    "unique_phones": 2_305_025,
    "leads": 1_000_000,
    "available_leads": 750_000,
    "queue_depth": 812,
    "running_jobs": 6,
    "retryable_jobs": 4,
    "oldest_queue_secs": 1200,
    "added_last_hour": 4_215,
    "empty_rate": 0.18,
}

ACTIVITY = {
    "queue_depth": 812,
    "running_jobs": 6,
    "retryable_jobs": 4,
    "jobs_last_minute": 22,
    "jobs_last_five_minutes": 118,
    "results_last_minute": 640,
    "latest_result_at": "2026-07-28T11:59:55+00:00",
    "businesses_last_minute": 310,
    "businesses_last_five_minutes": 1_602,
    "businesses_total": 9_244_326,
    "latest_business_at": "2026-07-28T11:59:58+00:00",
    "latest_keyword": "dentist",
    "latest_state": "PA",
    "latest_result_count": 20,
    "latest_job_at": "2026-07-28T11:59:55+00:00",
    "healthy_workers": 6,
    "latest_write_at": "2026-07-28T11:59:58+00:00",
    "write_age": "2s",
    "write_is_fresh": True,
}

PIPELINE_STATE = {
    "key": "running",
    "label": "Running",
    "detail": "Workers are processing the queue",
}

PAUSE_INFO = {"mode": "", "cancelled_jobs": 0}

PIPELINE_EVENTS = [
    {
        "job_id": 5_001,
        "created_at": "2026-07-28T11:59:40+00:00",
        "keyword": "dentist",
        "state": "PA",
        "result_count": 20,
        "phone_count": 17,
    },
    {
        "job_id": 5_002,
        "created_at": "2026-07-28T11:59:55+00:00",
        "keyword": "orthodontist",
        "state": "TX",
        "result_count": 18,
        "phone_count": 12,
    },
]

WORKERS = [
    {
        "box_id": "box-1",
        "container_name": "gms-worker-1",
        "reported_at": "2026-07-28T11:59:50+00:00",
        "heartbeat_age": "10s",
        "is_healthy": True,
        "status": "alive",
        "active_jobs": 1,
        "jobs_processed": 4_210,
        "results_per_min": 18.5,
        "current_state": "PA",
        "current_keyword": "dentist",
        "current_job_id": 5_001,
    },
    {
        "box_id": "box-2",
        "container_name": "gms-worker-2",
        "reported_at": "2026-07-28T11:55:00+00:00",
        "heartbeat_age": "5m",
        "is_healthy": False,
        "status": "stale",
        "active_jobs": 0,
        "jobs_processed": 3_900,
        "results_per_min": 0.0,
        "current_state": None,
        "current_keyword": None,
        "current_job_id": None,
    },
]

TRENDS = [
    {
        "label": f"{hour:02d}:00",
        "businesses": 100 + hour,
        "jobs": 20 + hour,
        "queue": 400 + hour,
        "cpu": 40.0,
        "memory": 70.0,
        "businesses_height": 80.0,
        "jobs_height": 60.0,
        "queue_height": 50.0,
    }
    for hour in range(24)
]

INCIDENTS = [
    {
        "checked_at": "2026-07-28T11:30:00+00:00",
        "status": "error",
        "messages": ["Queue depth exceeds its warning threshold"],
    },
    {
        "checked_at": "2026-07-28T10:30:00+00:00",
        "status": "ok",
        "messages": [],
    },
]

TOP_STATES = [
    {"state": "TX", "businesses": 1_204_002},
    {"state": "PA", "businesses": 980_411},
]

REGION_PAYLOADS: dict[str, dict] = {
    "overall": {"stack_status": STACK_STATUS},
    "stack": {
        "sample": SAMPLE,
        "stack_status": STACK_STATUS,
        "services": SERVICES,
    },
    "stats": {"stats": STATS},
    "activity": {
        "activity": ACTIVITY,
        "pipeline_state": PIPELINE_STATE,
        "pause_info": PAUSE_INFO,
    },
    "log": {"pipeline_events": PIPELINE_EVENTS},
    "workers": {"workers": WORKERS, "expected_workers": 8},
    "trends": {"trends": TRENDS},
    "incidents": {"incidents": INCIDENTS},
    "top-states": {"top_states": TOP_STATES},
}

STATE_CARDS_FRAGMENT = """
<div class="state-grid">
  <a href="/states/pa" class="state-card">
    <div class="state-card-head">
      <span class="state-code">PA</span>
    </div>
    <strong>161,863</strong><small>businesses</small>
    <div class="progress-meta"><span>Coverage</span><b>50%</b></div>
    <div class="progress"><span style="width:50%"></span></div>
    <div class="state-card-foot">
      <span>110/220 cells</span><span>25 keywords</span>
    </div>
  </a>
  <a href="/states/oh" class="state-card">
    <div class="state-card-head">
      <span class="state-code">OH</span>
    </div>
    <strong>136,150</strong><small>businesses</small>
    <div class="progress-meta"><span>Coverage</span><b>100%</b></div>
    <div class="progress"><span style="width:100%"></span></div>
    <div class="state-card-foot">
      <span>240/240 cells</span><span>25 keywords</span>
    </div>
  </a>
</div>
"""

STATE_KEYWORDS_FRAGMENT = """
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Keyword</th><th>Businesses</th><th>Cells</th>
        <th>Coverage</th><th>Empty rate</th><th>Last enqueue</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>24 Hour Pharmacy</strong></td>
        <td>124</td><td>110/220</td>
        <td>
          <div class="inline-progress"><span style="width:50%"></span></div>
          <small>50%</small>
        </td>
        <td><span class="rate">12.5%</span></td>
        <td>Jul 28, 11:59</td>
      </tr>
      <tr>
        <td><strong>Abatement Service</strong></td>
        <td>38</td><td>0/220</td>
        <td>
          <div class="inline-progress"><span style="width:0%"></span></div>
          <small>0%</small>
        </td>
        <td><span class="rate bad">75.0%</span></td>
        <td>—</td>
      </tr>
    </tbody>
  </table>
</div>
"""

STATE_CELLS_FRAGMENT = """
<div class="cell-grid">
  <span class="cell cell-posted"
        title="40.000000,-80.000000 · posted"></span>
  <span class="cell cell-reserved"
        title="40.000000,-79.750000 · reserved"></span>
  <span class="cell cell-failed"
        title="40.000000,-79.500000 · failed"></span>
  <span class="cell cell-uncovered"
        title="40.000000,-79.250000 · uncovered"></span>
</div>
"""


def aggregate_payload() -> dict:
    """What ``GET /api/dashboard`` returns: every region merged."""

    payload: dict = {}
    for region in REGION_PAYLOADS.values():
        payload.update(region)
    return payload


def paused_activity(cancelled_jobs: int = 0, mode: str = "drain") -> dict:
    state = (
        {
            "key": "paused",
            "label": "Paused",
            "detail": f"Queue cleared ({cancelled_jobs} cancelled)",
        }
        if mode == "clear"
        else {
            "key": "paused",
            "label": "Paused",
            "detail": "No new scrape jobs will be queued",
        }
    )
    return {
        "activity": {**ACTIVITY, "queue_depth": 0 if mode == "clear" else 812},
        "pipeline_state": state,
        "pause_info": {"mode": mode, "cancelled_jobs": cancelled_jobs},
    }


class ScraperFake:
    """Records every upstream call and answers the projection contract.

    ``failing`` names regions that should behave as if the upstream region is
    down, which is how the per-region isolation tests make one panel fail
    without touching the other eight.
    """

    def __init__(
        self,
        *,
        failing: set[str] | None = None,
        coverage_failing: set[str] | None = None,
        offline: bool = False,
        activity_after_write: dict | None = None,
    ) -> None:
        self.failing = failing or set()
        self.coverage_failing = coverage_failing or set()
        self.offline = offline
        self.activity_after_write = activity_after_write
        self.calls: list[httpx.Request] = []
        self.writes: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if self.offline:
            raise httpx.ConnectError("scraper unreachable", request=request)

        if path == "/dashboard/pipeline":
            self.writes.append(request.content)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text="<section>ok</section>",
            )
        if path == "/api/dashboard":
            return self._json(aggregate_payload())
        if path.startswith("/api/dashboard/"):
            region = path.rsplit("/", 1)[-1]
            if region in self.failing:
                return httpx.Response(503, text="region unavailable")
            if region == "activity" and self.activity_after_write and self.writes:
                return self._json(self.activity_after_write)
            payload = REGION_PAYLOADS.get(region)
            if payload is None:
                return httpx.Response(404, text="unknown region")
            return self._json(payload)
        if path == "/frag/states/cards":
            return self._fragment("cards", STATE_CARDS_FRAGMENT)
        if path.startswith("/frag/states/") and path.endswith("/keywords"):
            return self._fragment("keywords", STATE_KEYWORDS_FRAGMENT)
        if path.startswith("/frag/states/") and path.endswith("/cells"):
            return self._fragment("cells", STATE_CELLS_FRAGMENT)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html><body>GMS/OPS ready</body></html>",
        )

    @staticmethod
    def _json(payload: dict) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload).encode(),
        )

    def _fragment(self, key: str, content: str) -> httpx.Response:
        if key in self.coverage_failing:
            return httpx.Response(503, text=f"{key} unavailable")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=content,
        )
