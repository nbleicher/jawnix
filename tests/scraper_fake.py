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

from jawnix.states import US_STATES


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

# The live dashboard sends null for both fields when nothing is paused — a ""
# here once hid a Pydantic rejection that took out the whole monitoring screen
# in production (the null-pause-mode defect fixed alongside issue #70's smoke).
PAUSE_INFO = {"mode": None, "cancelled_jobs": None}

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

CAMPAIGN_HISTORY = [
    {
        "keyword": "Acoustic Guitar Lessons",
        "state": "OH",
        "cells_posted": 240,
        "first_enqueued": "Jul 27, 13:41",
        "latest_enqueued": "Jul 29, 00:00",
        "campaign_date": "Jul 29, 2026",
    },
    {
        "keyword": "Farm Equipment Dealer",
        "state": "KY",
        "cells_posted": 324,
        "first_enqueued": "Jul 28, 01:49",
        "latest_enqueued": "Jul 29, 00:03",
        "campaign_date": "Jul 29, 2026",
    },
    {
        "keyword": "Plumbers",
        "state": "PA",
        "cells_posted": 110,
        "first_enqueued": "Jul 20, 09:00",
        "latest_enqueued": "Jul 28, 17:30",
        "campaign_date": "Jul 28, 2026",
    },
]

RUNTIME_CONFIGURATION = {
    "states": ["KY", "OH"],
    "settings": {
        "zoom": 15,
        "radius": 10_000.0,
        "depth": 3,
        "lang": "en",
        "fast_mode": True,
        "timeout": 300,
    },
    "queue": {
        "target_depth": 50,
        "target_per_worker": 25,
        "min_target_depth": 25,
        "max_target_depth": 500,
        "batch_size": 100,
        "poll_secs": 5,
        "skip_recent_days": 0,
    },
    "overrides": {},
}

STATE_CELL_COUNTS = {
    "AK": 180,
    "AZ": 99,
    "KY": 324,
    "MO": 143,
    "OH": 240,
    "PA": 220,
    "TX": 500,
}

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

DATABASE_BROWSE = """
<div id="database-browse">
  <div class="table-wrap"><table><tbody>
    <tr>
      <td><strong>Buckeye Plumbing</strong><a href="https://buckeye.example">https://buckeye.example</a></td>
      <td>(614) 555-0101</td><td>OH</td><td>plumbers</td><td>Jul 28, 11:59</td>
    </tr>
    <tr>
      <td><strong>Capital Electric</strong></td>
      <td>614-555-0101</td><td>OH</td><td>electricians</td><td>Jul 28, 11:58</td>
    </tr>
  </tbody></table></div>
  <div class="pagination">
    <button disabled>Previous</button><span>Page 1</span><button>Next</button>
  </div>
</div>
"""

DATABASE_PAGE = f"""
<html><body>
  <div class="lead-totals">
    <span><b>9,244,326</b> businesses</span>
    <span><b>2,305,025</b> exportable phones</span>
  </div>
  <section>
    <div class="state-grid database-state-grid">
      <div class="state-card-wrap">
        <a href="/database/states/oh" class="state-card database-state-card">
          <div class="state-card-head"><span class="state-code">OH</span></div>
          <strong>136,150</strong><small>total businesses</small>
          <div class="database-card-stats">
            <span><b>71,204</b> unique phones</span>
            <span><b>25</b> niches</span>
          </div>
        </a>
      </div>
      <div class="state-card-wrap">
        <a href="/database/states/PA" class="state-card database-state-card">
          <div class="state-card-head"><span class="state-code">PA</span></div>
          <strong>161,863</strong><small>total businesses</small>
          <div class="database-card-stats">
            <span><b>84,110</b> unique phones</span>
            <span><b>24</b> niches</span>
          </div>
        </a>
      </div>
    </div>
  </section>
  <section class="section-block">
    <div class="section-head"><div><h2>Browse records</h2></div><span class="count-pill">51</span></div>
    {DATABASE_BROWSE}
  </section>
</body></html>
"""

DATABASE_STATE_PAGE = """
<html><body>
  <h1>OH database</h1>
  <div class="database-summary">
    <div><span>Total businesses</span><strong>136,150</strong></div>
    <div><span>Unique phones</span><strong>71,204</strong></div>
    <div><span>Niches</span><strong>2</strong></div>
  </div>
  <table><tbody>
    <tr>
      <td><input class="niche-checkbox" name="keyword" value="plumbers"></td>
      <td><strong>plumbers</strong></td><td>80,000</td><td>42,000</td><td></td>
    </tr>
    <tr>
      <td><input class="niche-checkbox" name="keyword" value="__uncategorized__"></td>
      <td><strong>Uncategorized</strong></td><td>56,150</td><td>29,204</td><td></td>
    </tr>
  </tbody></table>
</body></html>
"""

EXPORT_FILES_FRAGMENT = """
<div class="notice success"><span>OH.csv regenerated</span></div>
<div class="export-grid">
  <article class="export-card">
    <div><strong>OH.csv</strong><small>42.5 KB</small></div>
  </article>
  <article class="export-card">
    <div><strong>PA.csv</strong><small>38.0 KB</small></div>
  </article>
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
        keywords: list[str] | None = None,
        ai_enabled: bool = True,
        generation_error: str | None = None,
        runtime_failing: set[str] | None = None,
    ) -> None:
        self.failing = failing or set()
        self.coverage_failing = coverage_failing or set()
        self.offline = offline
        self.activity_after_write = activity_after_write
        self.keywords = list(keywords or KEYWORDS)
        self.ai_enabled = ai_enabled
        self.generation_error = generation_error
        self.runtime_failing = runtime_failing or set()
        self.rollover_enabled = False
        self.drafts: dict[str, list[str]] = {}
        self.calls: list[httpx.Request] = []
        self.writes: list[bytes] = []
        self.keyword_writes: list[dict[str, str]] = []
        self.runtime = json.loads(json.dumps(RUNTIME_CONFIGURATION))
        self.runtime_writes: list[dict[str, list[str]]] = []

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
        if path == "/frag/history/table":
            if "history" in self.runtime_failing:
                return httpx.Response(503, text="history unavailable")
            return self._html(self._history_table(request))
        if path == "/configure" and request.method == "GET":
            if "configure" in self.runtime_failing:
                return httpx.Response(503, text="configure unavailable")
            return self._html(self._configure_page())
        if path == "/configure/preview":
            if "preview" in self.runtime_failing:
                return httpx.Response(503, text="preview unavailable")
            proposed = self._runtime_from_form(request)
            return self._html(self._runtime_preview(proposed))
        if path == "/configure/save":
            if "save" in self.runtime_failing:
                return httpx.Response(503, text="save unavailable")
            proposed = self._runtime_from_form(request)
            self.runtime = proposed
            self.runtime_writes.append(self._multi_form(request))
            return self._html(self._runtime_preview(proposed, saved=True))
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
        if path == "/frag/states/cards":
            return self._fragment("cards", STATE_CARDS_FRAGMENT)
        if path.startswith("/frag/states/") and path.endswith("/keywords"):
            return self._fragment("keywords", STATE_KEYWORDS_FRAGMENT)
        if path.startswith("/frag/states/") and path.endswith("/cells"):
            return self._fragment("cells", STATE_CELLS_FRAGMENT)
        if path == "/database":
            return self._html(DATABASE_PAGE)
        if path == "/database/states/oh" and request.method == "GET":
            return self._html(DATABASE_STATE_PAGE)
        if path == "/database/export/oh" and request.method == "POST":
            return self._html(EXPORT_FILES_FRAGMENT)
        if path == "/database/states/oh/download":
            scope = request.url.params.get("scope", "all")
            keywords = request.url.params.get_list("keyword")
            if scope == "selected" and not keywords:
                return httpx.Response(422, text="Select at least one niche")
            label = (
                "all"
                if scope == "all"
                else keywords[0]
                if len(keywords) == 1
                else f"{len(keywords)}-niches"
            )
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "text/csv",
                    "Content-Disposition": (
                        f'attachment; filename="OH-{label}-phone-leads-2026-07-29.csv"'
                    ),
                },
                text=(
                    "business_name,phone_number,state\n"
                    "Buckeye Plumbing,6145550101,OH\n"
                ),
            )
        if path == "/database/bulk-download":
            states = [
                value.upper()
                for value in request.url.params.get_list("state")
            ]
            label = (
                "-".join(states)
                if len(states) <= 4
                else f"{len(states)}-states"
            )
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "text/csv",
                    "Content-Disposition": (
                        f'attachment; filename="{label}-phone-leads-2026-07-29.csv"'
                    ),
                },
                text=(
                    "business_name,phone_number,state\n"
                    + "".join(
                        f"{state} Business,5550000000,{state}\n"
                        for state in states
                    )
                ),
            )
        if path in {
            "/database/download/OH.csv",
            "/database/download/PA.csv",
        }:
            filename = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "text/csv",
                    "Content-Disposition": (
                        f'attachment; filename="{filename}"'
                    ),
                },
                text="phone,title\n6145550101,Buckeye Plumbing\n",
            )
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
    def _multi_form(request: httpx.Request) -> dict[str, list[str]]:
        return parse_qs(request.content.decode(), keep_blank_values=True)

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

    def _history_table(self, request: httpx.Request) -> str:
        search = request.url.params.get("search", "").casefold()
        state = request.url.params.get("state", "").upper()
        sort = request.url.params.get("sort", "last_enqueued")
        reverse = request.url.params.get("direction", "desc") != "asc"
        rows = [
            row
            for row in CAMPAIGN_HISTORY
            if (not search or search in row["keyword"].casefold())
            and (not state or state == row["state"])
        ]
        sort_key = {
            "keyword": "keyword",
            "state": "state",
            "cells_posted": "cells_posted",
            "latest_enqueued": "latest_enqueued",
            "last_enqueued": "campaign_date",
        }.get(sort, "campaign_date")
        rows.sort(key=lambda item: item[sort_key], reverse=reverse)
        body = "".join(
            f"""
            <tr>
              <td><strong>{row["keyword"]}</strong></td>
              <td><span class="state-code small">{row["state"]}</span></td>
              <td>{row["cells_posted"]}</td>
              <td>{row["first_enqueued"]}</td>
              <td>{row["latest_enqueued"]}</td>
              <td>{row["campaign_date"]}</td>
            </tr>
            """
            for row in rows
        )
        if not body:
            body = (
                '<tr><td colspan="6" class="table-empty">'
                "No campaign history</td></tr>"
            )
        return f"""
        <div class="table-wrap"><table>
          <thead><tr>
            <th>Keyword</th><th>State</th><th>Cells posted</th>
            <th>First enqueue</th><th>Latest enqueue</th>
            <th>Campaign date</th>
          </tr></thead>
          <tbody>{body}</tbody>
        </table></div>
        """

    def _runtime_cells(self, configuration: dict) -> list[tuple[str, int]]:
        rows = []
        for state in configuration["states"]:
            base = STATE_CELL_COUNTS.get(state, 100)
            cell_size = (
                configuration["overrides"]
                .get(state, {})
                .get("cell_size_km")
            )
            cells = (
                max(1, round(base * 15 / float(cell_size)))
                if cell_size
                else base
            )
            rows.append((state, cells))
        return rows

    def _runtime_preview(
        self,
        configuration: dict,
        *,
        saved: bool = False,
    ) -> str:
        notice = (
            '<div class="notice success"><span>Configuration saved</span></div>'
            if saved
            else ""
        )
        rows = "".join(
            (
                '<div><span class="state-code small">'
                f"{state}</span><strong>{cells:,}</strong></div>"
            )
            for state, cells in self._runtime_cells(configuration)
        )
        return f"""
        {notice}
        <div class="section-head"><div>
          <p class="eyebrow">Computed</p><h2>Cell count</h2>
        </div></div>
        <div class="preview-list">{rows}</div>
        """

    def _configure_page(self) -> str:
        state_inputs = "".join(
            (
                '<label><input type="checkbox" name="states" '
                f'value="{state.lower()}"'
                f'{" checked" if state in self.runtime["states"] else ""}>'
                f"<span>{state}</span></label>"
            )
            for state in sorted(US_STATES)
        )
        settings = self.runtime["settings"]
        queue = self.runtime["queue"]
        overrides = "".join(
            f"""
            <div class="override-row"><strong>{state}</strong>
              <label>Cell size (km)<input type="number"
                name="cell_size_km_{state.lower()}"
                value="{self.runtime["overrides"].get(state, {}).get("cell_size_km", "")}">
              </label>
              <label>Zoom<input type="number"
                name="zoom_{state.lower()}"
                value="{self.runtime["overrides"].get(state, {}).get("zoom", "")}">
              </label>
            </div>
            """
            for state in self.runtime["states"]
        )
        return f"""
        <html><body>
          <form>
            <div class="state-picker">{state_inputs}</div>
            <input name="zoom" value="{settings["zoom"]}">
            <input name="radius" value="{settings["radius"]}">
            <input name="depth" value="{settings["depth"]}">
            <input name="lang" value="{settings["lang"]}">
            <input name="fast_mode" type="checkbox"
              {"checked" if settings["fast_mode"] else ""}>
            <input name="timeout" value="{settings["timeout"]}">
            <input name="target_depth" value="{queue["target_depth"]}">
            <input name="target_per_worker"
              value="{queue["target_per_worker"]}">
            <input name="min_target_depth"
              value="{queue["min_target_depth"]}">
            <input name="max_target_depth"
              value="{queue["max_target_depth"]}">
            <input name="batch_size" value="{queue["batch_size"]}">
            <input name="poll_secs" value="{queue["poll_secs"]}">
            <input name="skip_recent_days"
              value="{queue["skip_recent_days"]}">
            <div class="override-grid">{overrides}</div>
            {self._runtime_preview(self.runtime)}
          </form>
        </body></html>
        """

    def _runtime_from_form(self, request: httpx.Request) -> dict:
        form = self._multi_form(request)

        def one(name: str, default: object) -> str:
            values = form.get(name)
            return values[-1] if values else str(default)

        states = [value.upper() for value in form.get("states", [])]
        overrides = {}
        for state in states:
            code = state.lower()
            values = {}
            cell_size = one(f"cell_size_km_{code}", "").strip()
            zoom = one(f"zoom_{code}", "").strip()
            if cell_size:
                values["cell_size_km"] = float(cell_size)
            if zoom:
                values["zoom"] = int(float(zoom))
            if values:
                overrides[state] = values
        return {
            "states": states,
            "settings": {
                "zoom": int(float(one("zoom", 15))),
                "radius": float(one("radius", 10_000)),
                "depth": int(float(one("depth", 3))),
                "lang": one("lang", "en"),
                "fast_mode": one("fast_mode", "") == "on",
                "timeout": int(float(one("timeout", 300))),
            },
            "queue": {
                "target_depth": int(float(one("target_depth", 50))),
                "target_per_worker": int(
                    float(one("target_per_worker", 25))
                ),
                "min_target_depth": int(
                    float(one("min_target_depth", 25))
                ),
                "max_target_depth": int(
                    float(one("max_target_depth", 500))
                ),
                "batch_size": int(float(one("batch_size", 100))),
                "poll_secs": int(float(one("poll_secs", 5))),
                "skip_recent_days": int(
                    float(one("skip_recent_days", 0))
                ),
            },
            "overrides": overrides,
        }

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
