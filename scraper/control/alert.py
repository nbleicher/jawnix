#!/usr/bin/env python3
"""
alert.py — push alerts when the pipeline drifts (O-10)
======================================================
Runs on the CONTROL VPS on a timer (e.g. every 10 min). Where monitor.py is a
*pull* dashboard you look at, this *pushes* an alert when something is wrong, so
the system can run unattended.

Checks (each one only runs if its inputs/thresholds are configured):
  • API reachable        — GET <api-base>/api/v1/jobs responds
  • DB reachable         — can connect to Postgres
  • Queue depth in band  — pending jobs between --min-depth and --max-depth
  • On pace              — businesses added in the last hour ≥ pace-floor × (target/24)
  • Block/empty rate     — share of last-hour jobs returning 0 results ≤ --max-empty-rate

Output:
  • If a webhook is set (--webhook or $ALERT_WEBHOOK), POST {"text": "..."} (Slack-compatible).
  • Always print to stderr and exit non-zero when there are alerts (so cron/systemd flag it).
  • Clean run prints "OK" and exits 0.

Usage (systemd timer runs this):
  DATABASE_URL=... ALERT_WEBHOOK=https://hooks.slack.com/... \
    python3 alert.py --api-base http://localhost:8080 --target 250000 \
                     --min-depth 1 --max-depth 50000 --max-empty-rate 0.5
"""

import argparse
import json
import os
import sys
import time
from urllib.request import Request, urlopen


def api_pending(api_base: str, api_key: str = None):
    """Approximate pending count for reachability-only fallback."""
    try:
        req = Request(api_base.rstrip("/") + "/api/v1/jobs")
        if api_key:
            req.add_header("X-API-Key", api_key)
        resp = urlopen(req, timeout=10)
        data = json.load(resp) or {}
        jobs = data.get("jobs", []) if isinstance(data, dict) else data
        n = sum(1 for j in jobs
                if str(j.get("status", j.get("Status", ""))).lower()
                in ("pending", "queued", "available", "scheduled", "retryable", ""))
        return n, None
    except Exception as e:
        return -1, str(e)


def db_metrics(dsn: str):
    """Return production, quality, and exact River queue metrics."""
    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM businesses "
                        "WHERE first_seen >= now() - interval '1 hour'")
            added = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FILTER (WHERE result_count = 0), count(*) "
                "FROM scrape_results WHERE created_at >= now() - interval '1 hour'")
            zero, total = cur.fetchone()
            empty_rate = (zero / total) if total else 0.0
            cur.execute(
                "SELECT count(*) FILTER (WHERE state IN ('available','pending','scheduled','retryable')), "
                "count(*) FILTER (WHERE state='running'), "
                "count(*) FILTER (WHERE state='retryable'), "
                "COALESCE(extract(epoch FROM now()-min(created_at) FILTER "
                "(WHERE state IN ('available','pending','retryable'))),0) "
                "FROM river_job WHERE kind='scrape'"
            )
            queued, running, retryable, oldest_secs = cur.fetchone()
        conn.close()
        return added, empty_rate, int(queued), int(running), int(retryable), float(oldest_secs), None
    except Exception as e:
        return None, None, None, None, None, None, str(e)


def spool_metrics(spool_dir: str):
    """Return pending marker count and oldest age, or None when not mounted."""
    if not os.path.isdir(spool_dir):
        return None, None
    count = 0
    oldest_mtime = None
    with os.scandir(spool_dir) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".ndjson.done"):
                count += 1
                mtime = entry.stat().st_mtime
                oldest_mtime = mtime if oldest_mtime is None else min(oldest_mtime, mtime)
    oldest_secs = max(0.0, time.time() - oldest_mtime) if oldest_mtime is not None else 0.0
    return count, oldest_secs


def send_webhook(url: str, text: str):
    try:
        req = Request(url, data=json.dumps({"text": text}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"  webhook failed: {e}", file=sys.stderr)
        return False


def env_number(name: str, default, converter):
    value = os.environ.get(name)
    return converter(value) if value not in (None, "") else default


def record_event(dsn: str, status: str, messages: list[str]):
    if not dsn:
        return
    try:
        import psycopg2
        from psycopg2.extras import Json
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pipeline_alert_events (status, messages) VALUES (%s, %s)",
                    (status, Json(messages)),
                )
                cur.execute(
                    "DELETE FROM pipeline_alert_events "
                    "WHERE checked_at < NOW()-interval '30 days'"
                )
    except Exception as e:
        print(f"  alert history write failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Push alerts when the pipeline drifts (O-10)",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--api-base", default="http://localhost:8080")
    ap.add_argument("--api-key", default=os.environ.get("API_KEY"))
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--target", type=int, default=env_number("GMS_DAILY_TARGET", None, int),
                    help="Daily net-unique target (enables the pace check)")
    ap.add_argument("--pace-floor", type=float,
                    default=env_number("GMS_PACE_FLOOR", 0.4, float),
                    help="Alert if hourly rate < this × (target/24). Default 0.4")
    ap.add_argument("--min-depth", type=int,
                    default=env_number("GMS_QUEUE_MIN_DEPTH", 1, int),
                    help="Alert if pending jobs below this")
    ap.add_argument("--max-depth", type=int,
                    default=env_number("GMS_QUEUE_MAX_DEPTH", 500, int),
                    help="Alert if pending jobs above this")
    ap.add_argument("--max-queue-age-mins", type=float,
                    default=env_number("GMS_QUEUE_MAX_AGE_MINS", 30, float),
                    help="Alert if the oldest waiting job exceeds this age")
    ap.add_argument("--max-retryable", type=int,
                    default=env_number("GMS_MAX_RETRYABLE", 50, int),
                    help="Alert if retryable jobs exceed this count")
    ap.add_argument("--spool-dir", default=os.environ.get("GMS_SPOOL_DIR", "/data/incoming"),
                    help="NDJSON spool directory; skipped when it is not present")
    ap.add_argument("--max-spool-files", type=int,
                    default=env_number("GMS_MAX_SPOOL_FILES", 200, int),
                    help="Alert if completed files waiting for the shipper exceed this count")
    ap.add_argument("--max-spool-age-mins", type=float,
                    default=env_number("GMS_MAX_SPOOL_AGE_MINS", 15, float),
                    help="Alert if the oldest completed spool file exceeds this age")
    ap.add_argument("--max-empty-rate", type=float,
                    default=env_number("GMS_MAX_EMPTY_RATE", 0.9, float),
                    help="Alert if last-hour 0-result job rate exceeds this (e.g. 0.5)")
    ap.add_argument("--webhook", default=os.environ.get("ALERT_WEBHOOK"))
    args = ap.parse_args()

    alerts = []
    infrastructure_error = False

    # API + queue depth
    pending, api_err = api_pending(args.api_base, args.api_key)
    if api_err:
        infrastructure_error = True
        alerts.append(f"queue API unreachable ({args.api_base}): {api_err}")
    elif not args.dsn:
        if args.min_depth is not None and pending < args.min_depth:
            alerts.append(f"queue starved: {pending} pending < min {args.min_depth} "
                          f"(enqueuer down or campaign exhausted?)")
        if args.max_depth is not None and pending > args.max_depth:
            alerts.append(f"queue backing up: {pending} pending > max {args.max_depth} "
                          f"(workers down or too slow?)")

    # DB-derived: pace + empty rate
    if args.dsn:
        added, empty_rate, queued, running, retryable, oldest_secs, db_err = db_metrics(args.dsn)
        if db_err:
            infrastructure_error = True
            alerts.append(f"database unreachable: {db_err}")
        else:
            if args.min_depth is not None and queued + running < args.min_depth:
                alerts.append(
                    f"queue starved: {queued} queued + {running} running < min {args.min_depth}"
                )
            if args.max_depth is not None and queued > args.max_depth:
                alerts.append(f"queue backing up: {queued} queued > max {args.max_depth}")
            if args.max_queue_age_mins is not None and oldest_secs > args.max_queue_age_mins * 60:
                alerts.append(f"oldest queued job is {oldest_secs / 60:.0f}m old > {args.max_queue_age_mins:g}m")
            if args.max_retryable is not None and retryable > args.max_retryable:
                alerts.append(f"retry storm: {retryable} retryable jobs > max {args.max_retryable}")
            if args.target:
                expected_hr = args.target / 24.0
                if added < args.pace_floor * expected_hr:
                    alerts.append(f"behind pace: {added:,} new businesses in last hour "
                                  f"< {args.pace_floor:.0%} of {expected_hr:,.0f}/hr target")
            if args.max_empty_rate is not None and empty_rate > args.max_empty_rate:
                alerts.append(f"high empty-result rate: {empty_rate:.0%} of last-hour jobs "
                              f"returned 0 results > {args.max_empty_rate:.0%} (blocking? add/rotate proxies)")
    elif args.target or args.max_empty_rate is not None:
        alerts.append("pace/empty-rate checks skipped: no DATABASE_URL/--dsn")

    spool_count, spool_oldest_secs = spool_metrics(args.spool_dir)
    if spool_count is not None:
        if args.max_spool_files is not None and spool_count > args.max_spool_files:
            alerts.append(f"result spool backing up: {spool_count} files > max {args.max_spool_files}")
        if args.max_spool_age_mins is not None and spool_oldest_secs > args.max_spool_age_mins * 60:
            alerts.append(
                f"oldest completed spool file is {spool_oldest_secs / 60:.0f}m old "
                f"> {args.max_spool_age_mins:g}m"
            )

    if not alerts:
        record_event(args.dsn, "ok", [])
        print("OK")
        return 0

    record_event(args.dsn, "error" if infrastructure_error else "alert", alerts)
    msg = "⚠ Scraper alert:\n" + "\n".join(f"• {a}" for a in alerts)
    print(msg, file=sys.stderr)
    if args.webhook:
        send_webhook(args.webhook, msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
