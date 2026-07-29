"""A controlled Scraper fake that answers the real projection contract.

Every payload here is shaped exactly like the upstream dashboard's own context
for that region — same keys, same derived fields — so a test passing against
this fake is evidence about the real contract rather than about the fake. The
per-region split mirrors ``/api/dashboard/{region}``.
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import parse_qs

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

KEYWORDS = ["plumbers", "electricians"]
WINNERS = [
    {
        "keyword": "plumbers",
        "phone_businesses": 2_480,
        "businesses": 4_000,
        "posted_cells": 1_000,
        "phones_per_cell": 2.48,
        "phone_rate": 0.62,
        "last_used": "Jul 28",
    },
    {
        "keyword": "roof repair",
        "phone_businesses": 1_200,
        "businesses": 2_000,
        "posted_cells": 600,
        "phones_per_cell": 2.0,
        "phone_rate": 0.6,
        "last_used": "Jul 27",
    },
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
        offline: bool = False,
        activity_after_write: dict | None = None,
        keywords: list[str] | None = None,
        ai_enabled: bool = True,
        generation_error: str | None = None,
    ) -> None:
        self.failing = failing or set()
        self.offline = offline
        self.activity_after_write = activity_after_write
        self.keywords = list(keywords or KEYWORDS)
        self.ai_enabled = ai_enabled
        self.generation_error = generation_error
        self.rollover_enabled = False
        self.drafts: dict[str, list[str]] = {}
        self.calls: list[httpx.Request] = []
        self.writes: list[bytes] = []
        self.keyword_writes: list[dict[str, str]] = []

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
        if path == "/keywords/winners":
            return self._html(self._winners_page())
        if path == "/keywords" and request.method == "GET":
            draft_id = request.url.params.get("draft")
            draft = self.drafts.get(draft_id or "")
            return self._html(self._keywords_page(draft_id, draft))
        if path == "/keywords/generate":
            form = self._form(request)
            if self.generation_error:
                return self._html(
                    f'<div class="notice error"><span>{self.generation_error}</span></div>'
                )
            if not self.ai_enabled:
                return self._html(
                    '<div class="notice error"><span>AI generation is not configured</span></div>'
                )
            if (
                form.get("mode") == "adjacent"
                and form.get("seed_keyword", "").casefold()
                not in {item["keyword"].casefold() for item in WINNERS}
            ):
                return self._html(
                    '<div class="notice error"><span>The selected winner is unavailable</span></div>'
                )
            generation_id = str(uuid.uuid4())
            self.drafts[generation_id] = [
                f"Unused Service {index}" for index in range(1, 26)
            ]
            return httpx.Response(
                303,
                headers={"Location": f"/keywords?draft={generation_id}"},
            )
        if path == "/keywords/save":
            form = self._form(request)
            proposed = []
            seen = set()
            for line in form.get("text", "").splitlines():
                value = line.strip()
                key = value.casefold()
                if value and not value.startswith("#") and key not in seen:
                    seen.add(key)
                    proposed.append(value)
            if not proposed:
                return httpx.Response(422, json={"detail": "At least one keyword is required"})
            self.keywords = proposed
            self.keyword_writes.append(form)
            return self._html("<div>saved</div>")
        if path == "/keywords/auto-rollover":
            form = self._form(request)
            if form.get("action") == "enable" and not self.ai_enabled:
                return httpx.Response(
                    422,
                    json={"detail": "AI generation is not configured"},
                )
            self.rollover_enabled = form.get("action") == "enable"
            return self._html(self._rollover())
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
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html><body>GMS/OPS ready</body></html>",
        )

    @staticmethod
    def _form(request: httpx.Request) -> dict[str, str]:
        return {
            key: values[-1]
            for key, values in parse_qs(request.content.decode()).items()
        }

    @staticmethod
    def _html(document: str) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text=document,
        )

    def _rollover(self) -> str:
        if self.rollover_enabled:
            state = "working"
            label = "Current batch active"
            detail = "12 of 20 coverage jobs enqueued"
            percent = 60
            action = "Disable"
        else:
            state = "off"
            label = "Off"
            detail = "Manual keyword batches"
            percent = 60
            action = "Enable"
        return f"""
        <section class="keyword-rollover rollover-{state}">
          <div class="rollover-summary">
            <strong>{label}</strong><small>{detail}</small>
          </div>
          <div class="rollover-meter"><div><strong>{percent}%</strong></div></div>
          <div class="rollover-event">
            <strong class="event-generated">Generated</strong>
            <small>Jul 28 · 12:00 UTC</small>
          </div>
          <button class="button rollover-control"><span>{action}</span></button>
        </section>
        """

    def _keywords_page(
        self,
        draft_id: str | None = None,
        draft: list[str] | None = None,
    ) -> str:
        values = draft if draft is not None else self.keywords
        disabled = "" if self.ai_enabled else " disabled"
        generation = ""
        hidden = ""
        if draft_id and draft is not None:
            generation = """
              <div class="notice ai-notice"><span>
                <strong>Draft ready:</strong> 25 broad local-business keywords.
                Review below; nothing has been saved or enqueued.
                7 candidates were filtered.
              </span></div>
            """
            hidden = (
                f'<input type="hidden" name="generation_id" value="{draft_id}">'
            )
        return f"""
        <html><body>
          {self._rollover()}
          {generation}
          <textarea id="keyword-text">{"\n".join(values)}</textarea>
          {hidden}
          <button hx-post="/keywords/generate" hx-vals='{{"mode":"broad"}}'{disabled}>
            Generate 25
          </button>
        </body></html>
        """

    @staticmethod
    def _winners_page() -> str:
        rows = []
        for rank, winner in enumerate(WINNERS, 1):
            rows.append(
                f"""
                <tr>
                  <td>{rank}</td>
                  <td><strong>{winner["keyword"].title()}</strong>
                    <small>Last used {winner["last_used"]}</small></td>
                  <td>{winner["phone_businesses"]:,}</td>
                  <td>{winner["businesses"]:,}</td>
                  <td>{winner["posted_cells"]:,}</td>
                  <td>{winner["phones_per_cell"]:.2f}</td>
                  <td>{winner["phone_rate"]:.1%}</td>
                  <td><button hx-vals='{json.dumps({
                      "mode": "adjacent",
                      "seed_keyword": winner["keyword"],
                  })}'>Generate adjacent</button></td>
                </tr>
                """
            )
        return (
            '<div class="table-wrap winners-table"><table><tbody>'
            + "".join(rows)
            + "</tbody></table></div>"
        )

    @staticmethod
    def _json(payload: dict) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload).encode(),
        )
