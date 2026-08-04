#!/usr/bin/env python3
"""
enqueue.py — queue-era job producer with continuous top-up + idempotent ledger
==============================================================================
Runs on the CONTROL VPS. Reads active_states.yaml + keywords.txt, expands every
active state into grid cells, and inserts (keyword × cell) scrape jobs into the
River queue via the serve API (POST /api/v1/scrape).

Idempotency / campaign dedup (Postgres ledger — migration 20260618000000):
  • O-11: every enqueued (keyword, state, cell, day) is logged in `enqueue_log`
    (UNIQUE). Re-runs and restarts skip cells already queued today — no dup jobs.
  • O-12: `keyword_history` records when a full (keyword, state) campaign was
    last enqueued; with --skip-recent-days N (or queue.skip_recent_days), only
    completed pairs within the last N days are skipped.

The ledger is OPTIONAL: with --dry-run, --no-ledger, or no DATABASE_URL, the
enqueuer runs without it (no dedup), so dry-runs work offline.

Modes:
  --once        insert one top-up batch then exit (cron/daily)
  --watch       continuous top-up: keep the queue near target_depth forever
  --dry-run     expand + count only; insert nothing, touch no DB

Usage:
  python3 enqueue.py --dry-run
  DATABASE_URL=postgres://... python3 enqueue.py --watch
  python3 enqueue.py --once --skip-recent-days 7
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# Grid logic vendored in grid.py so this bundle is self-contained
# (no dependency on the auto_scrape/orchestrate.py repo).
sys.path.insert(0, str(HERE))
from grid import generate_grid_cells, STATE_CONFIG  # noqa: E402


# ── Inputs ────────────────────────────────────────────────────────────────────
def load_states(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text()) or {}
    cfg.setdefault("states", [])
    cfg.setdefault("settings", {})
    cfg.setdefault("queue", {})
    cfg.setdefault("overrides", {})
    cfg.setdefault("api_base", "http://localhost:8080")
    return cfg


# Legal-industry niches are permanently barred from campaigns (operator decision, Jul 2026).
# Enforced here as the choke point: nothing lawyer-related gets enqueued regardless of
# how it landed in keywords.txt (AI rollover, dashboard save, or manual edit).
BLOCKED_KEYWORDS = re.compile(
    r"\b(attorneys?|lawyers?|law|legal|paralegals?|litigation|solicitors?|barristers?|notary|notaries)\b",
    re.IGNORECASE,
)


def load_keywords(path: Path) -> list:
    if not path.exists():
        print(f"ERROR: keywords file not found: {path}", file=sys.stderr)
        sys.exit(1)
    out, seen = [], set()
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        kw = ln.strip()
        if not kw or kw.startswith("#") or kw.lower() in seen:
            continue
        if BLOCKED_KEYWORDS.search(kw):
            print(f"WARN: skipping blocked keyword: {kw}", file=sys.stderr)
            continue
        seen.add(kw.lower()); out.append(kw)
    return out


def resolve(state: str, overrides: dict):
    entry = STATE_CONFIG.get(state.lower())
    if not entry:
        return None, None
    bbox, cell_km = entry
    ov = overrides.get(state.lower(), {})
    return bbox, float(ov.get("cell_size_km", cell_km))


# ── Postgres ledger (O-11 idempotency + O-12 campaign dedup) ──────────────────
class Ledger:
    """Thin wrapper over enqueue_log + keyword_history. Lazy psycopg2 import so
    the rest of the tool works without the driver/DB."""

    def __init__(self, dsn: str):
        import psycopg2  # lazy
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def load_done(self, day: date, states: list) -> set:
        """Return {(keyword, state, cell)} already posted or freshly reserved today."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT keyword, state, cell FROM enqueue_log "
                "WHERE day = %s AND state = ANY(%s) "
                "AND (status = 'posted' OR (status = 'reserved' AND updated_at >= NOW() - interval '15 minutes'))",
                (day, [s.lower() for s in states]),
            )
            return {(r[0], r[1], r[2]) for r in cur.fetchall()}

    def load_recent(self, skip_days: int) -> set:
        """Return {(keyword, state)} fully enqueued within the last skip_days."""
        if skip_days <= 0:
            return set()
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT keyword, state FROM keyword_history "
                "WHERE last_enqueued >= (CURRENT_DATE - %s::int)",
                (skip_days,),
            )
            return {(r[0], r[1]) for r in cur.fetchall()}

    def queue_depth(self) -> int:
        """Return active scrape jobs directly from River when Postgres is available."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM river_job "
                "WHERE kind = 'scrape' "
                "AND state IN ('available', 'pending', 'scheduled', 'retryable')"
            )
            return int(cur.fetchone()[0])

    def alive_workers(self, stale_secs: int = 180) -> int:
        """Return workers with a fresh healthy heartbeat."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM worker_heartbeats "
                "WHERE reported_at >= NOW() - (%s * interval '1 second') "
                "AND status IN ('alive', 'ok')",
                (stale_secs,),
            )
            return int(cur.fetchone()[0])

    def reserve(self, keyword: str, state: str, cell: str, day: date) -> bool:
        """Reserve one job before POSTing it. False means already posted/reserved."""
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO enqueue_log (keyword, state, cell, day, status, updated_at) "
                "VALUES (%s,%s,%s,%s,'reserved',NOW()) "
                "ON CONFLICT (keyword, state, cell, day) DO UPDATE SET "
                "status = 'reserved', updated_at = NOW(), last_error = NULL "
                "WHERE enqueue_log.status = 'failed' "
                "   OR (enqueue_log.status = 'reserved' AND enqueue_log.updated_at < NOW() - interval '15 minutes') "
                "RETURNING 1",
                (keyword, state, cell, day),
            )
            return cur.fetchone() is not None

    def mark_posted(self, rows: list, day: date, expected_cells: dict):
        """Mark successful POSTs and complete keyword_history for full campaigns."""
        if not rows:
            return
        with self.conn.cursor() as cur:
            cur.executemany(
                "UPDATE enqueue_log SET status = 'posted', updated_at = NOW(), last_error = NULL "
                "WHERE keyword = %s AND state = %s AND cell = %s AND day = %s",
                [(kw, st, cell, day) for (kw, st, cell) in rows],
            )
            pairs = {(kw, st) for (kw, st, _) in rows}
            complete = []
            for kw, st in pairs:
                total_cells = expected_cells.get((kw, st), 0)
                if not total_cells:
                    continue
                cur.execute(
                    "SELECT count(DISTINCT cell) FROM enqueue_log "
                    "WHERE keyword = %s AND state = %s AND day = %s",
                    (kw, st, day),
                )
                if cur.fetchone()[0] >= total_cells:
                    complete.append((kw, st, day))
            if complete:
                cur.executemany(
                    "INSERT INTO keyword_history (keyword, state, last_enqueued) VALUES (%s,%s,%s) "
                    "ON CONFLICT (keyword, state) DO UPDATE SET last_enqueued = EXCLUDED.last_enqueued",
                    complete,
                )

    def mark_failed(self, keyword: str, state: str, cell: str, day: date, err: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE enqueue_log SET status = 'failed', updated_at = NOW(), last_error = %s "
                "WHERE keyword = %s AND state = %s AND cell = %s AND day = %s",
                (err[:500], keyword, state, cell, day),
            )

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


# ── Build the full job list (keyword × cell across active states) ─────────────
def build_jobs(states_cfg: dict, keywords: list) -> list:
    s = states_cfg["settings"]
    overrides = states_cfg["overrides"]
    jobs_by_state = {}
    for state in states_cfg["states"]:
        state_key = state.lower()
        bbox, cell_km = resolve(state_key, overrides)
        if not bbox:
            print(f"  warn: unknown state '{state}', skipping")
            continue
        cells = generate_grid_cells(bbox, cell_km)
        ov = overrides.get(state_key, {})
        state_jobs = []
        for kw in keywords:
            for cell in cells:
                lat, lon = cell.split(",")
                state_jobs.append({
                    "keyword": kw,
                    "lang": s.get("lang", "en"),
                    "geo_coordinates": f"{lat},{lon}",
                    "zoom": int(ov.get("zoom", s.get("zoom", 15))),
                    "radius": float(s.get("radius", 10000)),
                    "max_depth": int(s.get("depth", 10)),   # API field is max_depth
                    "fast_mode": bool(s.get("fast_mode", False)),
                    "timeout": int(s.get("timeout", 300)),
                    "state": state_key,        # FR-4.4 provenance (needs the Go change)
                })
        jobs_by_state[state_key] = state_jobs

    jobs = []
    max_jobs = max((len(state_jobs) for state_jobs in jobs_by_state.values()), default=0)
    for idx in range(max_jobs):
        for state in states_cfg["states"]:
            state_jobs = jobs_by_state.get(state.lower(), [])
            if idx < len(state_jobs):
                jobs.append(state_jobs[idx])
    return jobs


def expected_cells_by_pair(jobs: list) -> dict:
    """Return {(keyword,state): number_of_cells} for the full campaign."""
    cells = {}
    for j in jobs:
        key = (j["keyword"], j["state"])
        cells.setdefault(key, set()).add(j["geo_coordinates"])
    return {key: len(value) for key, value in cells.items()}


def filter_jobs(jobs: list, recent: set, done: set) -> tuple:
    """Drop jobs already covered. Returns (kept, n_recent_skipped, n_done_skipped)."""
    kept, n_recent, n_done = [], 0, 0
    for j in jobs:
        key2 = (j["keyword"], j["state"])
        key3 = (j["keyword"], j["state"], j["geo_coordinates"])
        if key2 in recent:
            n_recent += 1
        elif key3 in done:
            n_done += 1
        else:
            kept.append(j)
    return kept, n_recent, n_done


# ── Queue API ─────────────────────────────────────────────────────────────────
def queue_depth(api_base: str, api_key: str = None) -> int:
    try:
        req = Request(api_base.rstrip("/") + "/api/v1/jobs")
        if api_key:
            req.add_header("X-API-Key", api_key)
        resp = urlopen(req, timeout=10)
        data = json.load(resp) or {}
        jobs = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        return sum(1 for j in jobs
                   if isinstance(j, dict) and
                   str(j.get("status", j.get("Status", ""))).lower()
                   in ("pending", "queued", "available", "scheduled", ""))
    except (URLError, HTTPError, Exception):
        return -1


def insert_job(api_base: str, job: dict, api_key: str = None) -> tuple[bool, str | None]:
    payload = {k: v for k, v in job.items() if not k.startswith("_")}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = Request(api_base.rstrip("/") + "/api/v1/scrape",
                  data=json.dumps(payload).encode(),
                  headers=headers, method="POST")
    try:
        resp = urlopen(req, timeout=15)
        if resp.status in (200, 201, 202):
            return True, None
        return False, f"unexpected HTTP status {resp.status}"
    except Exception as e:
        return False, str(e)


def top_up(api_base: str, jobs: list, cursor: int, target_depth: int, batch_size: int,
           ledger, day, expected_cells: dict, api_key: str = None,
           pause_file: Path | None = None) -> tuple:
    """Insert jobs until queue depth reaches target_depth or batch exhausted.
    Records successful inserts in the ledger. Returns (cursor, inserted, depth)."""
    if ledger:
        try:
            depth = ledger.queue_depth()
        except Exception as e:
            print(f"  warn: DB queue depth failed: {e}; falling back to API depth", file=sys.stderr)
            depth = queue_depth(api_base, api_key)
    else:
        depth = queue_depth(api_base, api_key)
    if depth < 0:
        print("  ERROR: queue API unreachable — is `serve` running?", file=sys.stderr)
        return cursor, 0, depth
    need = max(0, target_depth - depth)
    if need == 0:
        return cursor, 0, depth
    n = min(need, batch_size, len(jobs) - cursor)
    inserted, recorded = 0, []
    i = cursor
    stop = cursor + n
    while i < stop:
        if pause_file and pause_file.exists():
            break
        j = jobs[i]
        if ledger:
            try:
                reserved = ledger.reserve(j["keyword"], j["state"], j["geo_coordinates"], day)
            except Exception as e:
                print(f"  warn: ledger reserve failed: {e}", file=sys.stderr)
                reserved = True
            if not reserved:
                i += 1
                cursor = i
                continue
        ok, err = insert_job(api_base, j, api_key)
        if ok:
            inserted += 1
            recorded.append((j["keyword"], j["state"], j["geo_coordinates"]))
            i += 1
            cursor = i
            continue
        if ledger:
            try:
                ledger.mark_failed(j["keyword"], j["state"], j["geo_coordinates"], day, err or "POST failed")
            except Exception as e:
                print(f"  warn: ledger failure mark failed: {e}", file=sys.stderr)
        print(f"  warn: POST failed for {j['keyword']} {j['state']} {j['geo_coordinates']}: {err}",
              file=sys.stderr)
        break
    if ledger and recorded:
        try:
            ledger.mark_posted(recorded, day, expected_cells)
        except Exception as e:
            print(f"  warn: ledger record failed: {e}", file=sys.stderr)
    return cursor, inserted, depth


def prepare_jobs(args) -> tuple[dict, list, dict, object | None, date, str, int, int, int, str | None]:
    states_cfg = load_states(Path(args.states))
    keywords = load_keywords(Path(args.keywords))
    api_base = args.api_base or states_cfg["api_base"]
    api_key = args.api_key or states_cfg.get("api_key")
    q = states_cfg["queue"]
    target_depth = int(q.get("target_depth", 50))
    target_per_worker = int(q.get("target_per_worker", 25))
    min_target_depth = int(q.get("min_target_depth", 25))
    max_target_depth = int(q.get("max_target_depth", 500))
    batch_size = int(q.get("batch_size", 100))
    poll_secs = int(q.get("poll_secs", 5))
    skip_days = args.skip_recent_days if args.skip_recent_days is not None \
        else int(q.get("skip_recent_days", 0))

    jobs = build_jobs(states_cfg, keywords)
    total_built = len(jobs)
    expected_cells = expected_cells_by_pair(jobs)

    ledger, today = None, date.today()
    alive_workers = 0
    if not args.dry_run and not args.no_ledger and args.dsn:
        try:
            ledger = Ledger(args.dsn)
            alive_workers = ledger.alive_workers()
            if alive_workers:
                target_depth = max(
                    min_target_depth,
                    min(max_target_depth, alive_workers * target_per_worker),
                )
            recent = ledger.load_recent(skip_days)
            done = ledger.load_done(today, states_cfg["states"])
            jobs, n_recent, n_done = filter_jobs(jobs, recent, done)
            print(f"  Ledger: skipped {n_recent:,} (recent ≤{skip_days}d, O-12) + "
                  f"{n_done:,} (already queued/reserved today, O-11)")
        except Exception as e:
            print(f"  warn: ledger unavailable ({e}) — proceeding without dedup", file=sys.stderr)
            ledger = None
    elif not args.dry_run and not args.no_ledger and not args.dsn:
        print("  note: no DATABASE_URL — running without the dedup ledger (O-11/O-12 off).")

    by_state = {}
    for j in jobs:
        by_state[j["state"]] = by_state.get(j["state"], 0) + 1
    print(f"\n  Active states : {', '.join(states_cfg['states'])}")
    print(f"  Keywords      : {len(keywords)}")
    print(f"  Jobs to queue : {len(jobs):,} of {total_built:,} built  (keyword × cell)")
    for st, n_jobs in sorted(by_state.items(), key=lambda x: -x[1]):
        print(f"    {st.upper():3s} {n_jobs:>8,}")
    target_note = f" ({alive_workers} workers × {target_per_worker})" if alive_workers else " (fallback)"
    print(f"  Target depth  : {target_depth:,}{target_note}   batch {batch_size}   api {api_base}")

    return states_cfg, jobs, expected_cells, ledger, today, api_base, target_depth, batch_size, poll_secs, api_key


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Continuous top-up enqueuer (control plane)",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--states", default=str(HERE / "active_states.yaml"))
    ap.add_argument("--keywords", default=str(REPO / "keywords.txt"))
    ap.add_argument("--once", action="store_true", help="One top-up batch then exit")
    ap.add_argument("--watch", action="store_true", help="Continuous top-up loop")
    ap.add_argument("--dry-run", action="store_true", help="Expand + count only")
    ap.add_argument("--api-base", help="Override serve API base URL")
    ap.add_argument("--api-key", default=os.environ.get("API_KEY"),
                    help="API key for the serve API (default: $API_KEY)")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                    help="Postgres DSN for the dedup ledger (default: $DATABASE_URL)")
    ap.add_argument("--no-ledger", action="store_true", help="Disable the dedup ledger")
    ap.add_argument("--skip-recent-days", type=int, default=None,
                    help="Skip (keyword,state) enqueued within N days (O-12)")
    ap.add_argument("--pause-file", default=os.environ.get(
        "GMS_PIPELINE_PAUSE_FILE", str(HERE / "runtime" / "pipeline.paused")),
        help="when this marker exists, do not add new jobs")
    args = ap.parse_args()
    pause_file = Path(args.pause_file)

    states_cfg, jobs, expected_cells, ledger, today, api_base, target_depth, batch_size, poll_secs, api_key = prepare_jobs(args)

    if args.dry_run:
        print("\n  [dry-run] no jobs inserted, ledger untouched.")
        return
    if pause_file.exists() and not args.watch:
        print(f"\n  Pipeline paused by {pause_file}; no jobs inserted.")
        if ledger:
            ledger.close()
        return
    if not jobs and not args.watch:
        print("\n  Nothing to enqueue (all covered or filtered).");
        if ledger: ledger.close()
        return

    try:
        if args.watch:
            print("\n  Continuous top-up started — reloads states/keywords every cycle. Ctrl-C to stop.")
            if ledger:
                ledger.close()
                ledger = None
            was_paused = False
            while True:
                if pause_file.exists():
                    if not was_paused:
                        print(f"  Pipeline paused by {pause_file}; waiting.")
                    was_paused = True
                    time.sleep(poll_secs)
                    continue
                if was_paused:
                    print("  Pipeline resumed; queue top-up enabled.")
                    was_paused = False
                states_cfg, jobs, expected_cells, ledger, today, api_base, target_depth, batch_size, poll_secs, api_key = prepare_jobs(args)
                cursor = 0
                if not jobs:
                    print("  Nothing currently enqueueable; sleeping.")
                    if ledger:
                        ledger.close()
                    time.sleep(poll_secs)
                    continue
                cursor, inserted, depth = top_up(api_base, jobs, cursor, target_depth,
                                                 batch_size, ledger, today, expected_cells, api_key,
                                                 pause_file)
                if inserted:
                    print(f"  depth={depth} → inserted {inserted}  "
                          f"({cursor:,}/{len(jobs):,} used)")
                if ledger:
                    ledger.close()
                time.sleep(poll_secs)
        else:  # --once
            cursor = 0
            cursor, inserted, depth = top_up(api_base, jobs, cursor, target_depth,
                                             batch_size, ledger, today, expected_cells, api_key,
                                             pause_file)
            print(f"\n  depth was {depth}, inserted {inserted} job(s) toward target {target_depth}.")
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        if ledger:
            ledger.close()


if __name__ == "__main__":
    main()
