import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from .. import queries
from ..auth import require_user
from ..db import fetch, fetchrow, fetchval
from ..monitoring import build_trends, classify, duration, percent, service_rows
from .common import bridge, render

router = APIRouter(dependencies=[Depends(require_user)])


async def stats_context(request: Request) -> dict:
    row = await fetchrow(request, queries.DASHBOARD_STATS)
    return {"stats": dict(row)}


async def stack_context(request: Request) -> dict:
    sample_row = await fetchrow(request, queries.STACK_LATEST)
    alert_rows = [dict(row) for row in await fetch(request, queries.ALERT_EVENTS)]
    for alert in alert_rows:
        if isinstance(alert["messages"], str):
            alert["messages"] = json.loads(alert["messages"])
    sample = dict(sample_row) if sample_row else None
    latest_alert = alert_rows[0] if alert_rows else None
    status = classify(sample, latest_alert, request.app.state.settings)
    if sample:
        sample["memory_percent"] = percent(sample["memory_used_bytes"], sample["memory_total_bytes"])
        sample["disk_percent"] = percent(sample["disk_used_bytes"], sample["disk_total_bytes"])
        sample["uptime_label"] = duration(sample["host_uptime_seconds"])
        sample["spool_age_label"] = duration(sample["spool_oldest_seconds"])
    return {
        "sample": sample, "stack_status": status, "services": service_rows(sample),
        "incidents": alert_rows,
    }


async def workers_context(request: Request) -> dict:
    now = datetime.now(timezone.utc)
    stale_after = request.app.state.settings.worker_stale_secs
    rows = []
    for record in await fetch(request, queries.CURRENT_WORKERS):
        worker = dict(record)
        age = max(0, int((now - worker["reported_at"]).total_seconds()))
        worker["heartbeat_age"] = duration(age)
        worker["is_healthy"] = age <= stale_after and worker["status"] in {"alive", "ok"}
        rows.append(worker)
    sample_row = await fetchrow(request, queries.STACK_LATEST)
    expected = int(sample_row["expected_workers"]) if sample_row else request.app.state.settings.expected_workers
    return {"workers": rows, "expected_workers": expected}


async def activity_context(request: Request) -> dict:
    row = await fetchrow(request, queries.PIPELINE_ACTIVITY)
    activity = dict(row)
    paused = bridge(request).pipeline_paused()
    pause_info = bridge(request).pipeline_pause_info()
    outstanding = int(activity["queue_depth"] or 0) + int(activity["running_jobs"] or 0)
    if paused and pause_info["mode"] == "clear" and outstanding:
        count = int(pause_info["cancelled_jobs"])
        state = {
            "key": "pausing", "label": "Pausing",
            "detail": f"Queue cleared ({count} cancelled); running jobs are finishing",
        }
    elif paused and pause_info["mode"] == "clear":
        count = int(pause_info["cancelled_jobs"])
        state = {
            "key": "paused", "label": "Paused",
            "detail": f"Queue cleared ({count} cancelled); no new jobs will be queued",
        }
    elif paused and outstanding:
        state = {"key": "pausing", "label": "Pausing", "detail": "Queue refill stopped; current jobs are draining"}
    elif paused:
        state = {"key": "paused", "label": "Paused", "detail": "No new scrape jobs will be queued"}
    elif int(activity["healthy_workers"] or 0):
        detail = "Workers are processing the queue" if outstanding else "Enabled and waiting for work"
        state = {"key": "running", "label": "Running", "detail": detail}
    else:
        state = {"key": "stopped", "label": "No workers", "detail": "Pipeline is enabled but no healthy worker is reporting"}
    latest_write = activity.get("latest_write_at")
    if latest_write:
        age = max(0, int((datetime.now(timezone.utc) - latest_write).total_seconds()))
        activity["write_age"] = duration(age)
        activity["write_is_fresh"] = age <= 15
    else:
        activity["write_age"] = "never"
        activity["write_is_fresh"] = False
    return {"activity": activity, "pipeline_state": state, "pause_info": pause_info}


async def pipeline_log_context(request: Request) -> dict:
    return {"pipeline_events": [dict(row) for row in await fetch(request, queries.PIPELINE_LOG)]}


@router.get("/dashboard")
async def dashboard(request: Request):
    context = await stats_context(request)
    context.update(await stack_context(request))
    context.update(await workers_context(request))
    context.update(await activity_context(request))
    context.update(await pipeline_log_context(request))
    trend_rows = await fetch(request, queries.STACK_TRENDS)
    context["trends"] = build_trends(trend_rows)
    context["top_states"] = [dict(row) for row in await fetch(request, queries.TOP_STATES)]
    return render(request, "dashboard.html", **context)


@router.get("/frag/dashboard/stats")
async def dashboard_stats(request: Request):
    return render(request, "fragments/dashboard_stats.html", **await stats_context(request))


@router.get("/frag/dashboard/workers")
async def dashboard_workers(request: Request):
    return render(request, "fragments/workers.html", **await workers_context(request))


@router.get("/frag/dashboard/activity")
async def dashboard_activity(request: Request):
    return render(request, "fragments/pipeline_activity.html", **await activity_context(request))


@router.get("/frag/dashboard/log")
async def dashboard_log(request: Request):
    return render(request, "fragments/pipeline_log.html", **await pipeline_log_context(request))


@router.post("/dashboard/pipeline")
async def dashboard_pipeline_control(
    request: Request, action: str = Form(...), clear_queue: str = Form("no"),
):
    if action not in {"pause", "resume"}:
        raise HTTPException(status_code=422, detail="Unsupported pipeline action")
    if action == "pause":
        if clear_queue not in {"yes", "no"}:
            raise HTTPException(status_code=422, detail="Unsupported queue action")
        mode = "clear" if clear_queue == "yes" else "drain"
        await bridge(request).set_pipeline_paused(True, mode=mode)
        if mode == "clear":
            cancelled = await fetchval(request, queries.CANCEL_PENDING_SCRAPE_JOBS, rw=True)
            await bridge(request).set_pipeline_paused(
                True, mode=mode, cancelled_jobs=int(cancelled or 0),
            )
    else:
        await bridge(request).set_pipeline_paused(False)
    return render(request, "fragments/pipeline_activity.html", **await activity_context(request))


@router.get("/frag/dashboard/stack")
async def dashboard_stack(request: Request):
    return render(request, "fragments/stack_status.html", **await stack_context(request))


@router.get("/frag/dashboard/overall")
async def dashboard_overall(request: Request):
    return render(request, "fragments/overall_status.html", **await stack_context(request))


@router.get("/frag/dashboard/trends")
async def dashboard_trends(request: Request):
    rows = await fetch(request, queries.STACK_TRENDS)
    return render(request, "fragments/trends.html", trends=build_trends(rows))


@router.get("/frag/dashboard/incidents")
async def dashboard_incidents(request: Request):
    rows = [dict(row) for row in await fetch(request, queries.ALERT_EVENTS)]
    for row in rows:
        if isinstance(row["messages"], str):
            row["messages"] = json.loads(row["messages"])
    return render(request, "fragments/incidents.html", incidents=rows)


@router.get("/frag/dashboard/top-states")
async def dashboard_top_states(request: Request):
    rows = await fetch(request, queries.TOP_STATES)
    return render(request, "fragments/top_states.html", top_states=[dict(row) for row in rows])
