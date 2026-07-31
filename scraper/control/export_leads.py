#!/usr/bin/env python3
"""
export_leads.py — Postgres "global combine" + state/agent distribution (main.py model)
=======================================================================================
Postgres-native version of main.py's lead pipeline. Runs on the CONTROL VPS.

  --append            roll new businesses → leads (phone-deduped; state from AREA CODE
                      via derive_state(); flow='global_combine'). The cumulative master.
  --by-state [--out]  write the available pool to <out>/<STATE>.csv (phone,title)
                      — Postgres version of build_state_files / by_state/.
  --redistribute A,B  split the available pool (7-day re-distribution rule) across agents
                      into 10k-row CSVs, then mark those phones agent+date_distributed.
  --stats             manifest summary (total / available / distributed / by state).
  --daily             = --append then --by-state (cron default).

State, dedup, the 7-day window, and 10k chunking all match main.py exactly; the
manifest is the `leads` table and per-state grouping is a query, not folders.

Requires psycopg2 + DATABASE_URL (or --dsn). --dry-run prints the plan only.
"""

import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
EXPORT_DIR = Path(os.environ.get("EXPORT_DIR", REPO / "exports"))
ROWS_PER_FILE = 10_000

# state from the phone's area code (not the scrape state) — matches main.py.
APPEND_SQL = r"""
INSERT INTO leads (phone, title, state, flow, business_id)
SELECT DISTINCT ON (regexp_replace(b.phone, '\D', '', 'g'))
       regexp_replace(b.phone, '\D', '', 'g')      AS phone,
       b.title,
       derive_state(b.phone)                        AS state,
       'global_combine'                             AS flow,
       b.id
FROM businesses b
WHERE b.phone <> ''
  AND length(regexp_replace(b.phone, '\D', '', 'g')) = 10
ORDER BY regexp_replace(b.phone, '\D', '', 'g'), b.id
ON CONFLICT (phone) DO NOTHING;
"""


def get_conn(dsn):
    try:
        import psycopg2 as psycopg_driver
    except ImportError:
        import psycopg as psycopg_driver
    c = psycopg_driver.connect(dsn); c.autocommit = False
    return c


def chunked_write(rows, path_prefix: Path, day: str) -> list:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    paths, n = [], 0
    for i in range(0, len(rows), ROWS_PER_FILE):
        chunk = rows[i:i + ROWS_PER_FILE]; n += 1
        p = Path(f"{path_prefix}_{day}_{n}.csv")
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(["phone", "title"]); w.writerows(chunk)
        paths.append((p, len(chunk)))
    return paths


def do_append(cur):
    cur.execute(APPEND_SQL)
    return cur.rowcount


def do_by_state(cur, out_dir: Path):
    cur.execute("SELECT state, phone, title FROM available_leads ORDER BY state, phone")
    by = {}
    for state, phone, title in cur.fetchall():
        by.setdefault(state or "UNKNOWN", []).append((phone, title))
    out_dir.mkdir(parents=True, exist_ok=True)
    for state, rows in by.items():
        with open(out_dir / f"{state}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n"); w.writerow(["phone", "title"]); w.writerows(rows)
    return {s: len(r) for s, r in by.items()}


def do_redistribute(conn, agents: list, states_filter: set, day: str):
    with conn.cursor() as cur:
        if states_filter:
            cur.execute("SELECT phone, title, state, agent FROM available_leads "
                        "WHERE state = ANY(%s) ORDER BY phone", (sorted(states_filter),))
        else:
            cur.execute("SELECT phone, title, state, agent FROM available_leads ORDER BY phone")
        pool = cur.fetchall()
    if not pool:
        print("  pool is empty (after filters)."); return
    n = len(agents)
    target = len(pool) // n
    if target == 0:
        print(f"  only {len(pool)} leads for {n} agents — too few to split."); return

    # even split, skipping leads already currently held by the target agent (main.py rule)
    assignments = {a: [] for a in agents}
    used = set()
    for idx, agent in enumerate(agents):
        count = target + (1 if idx < (len(pool) % n) else 0)
        for (phone, title, state, cur_agent) in pool:
            if len(assignments[agent]) >= count:
                break
            if phone in used or cur_agent == agent:
                continue
            assignments[agent].append((phone, title)); used.add(phone)

    total = 0
    for agent, rows in assignments.items():
        if not rows:
            print(f"  {agent}: nothing to assign"); continue
        files = chunked_write(rows, EXPORT_DIR / agent, day)
        phones = [p for (p, _) in rows]
        with conn.cursor() as cur:
            cur.execute("UPDATE leads SET agent = %s, date_distributed = CURRENT_DATE "
                        "WHERE phone = ANY(%s)", (agent, phones))
        conn.commit()
        total += len(rows)
        print(f"  {agent}: {len(rows):,} leads → {len(files)} file(s)")
    print(f"  distributed {total:,} leads across {n} agent(s).")


def do_stats(cur):
    cur.execute("SELECT count(*) FROM leads"); total = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM available_leads"); avail = cur.fetchone()[0]
    cur.execute("SELECT state, count(*) FROM leads GROUP BY state ORDER BY count(*) DESC LIMIT 12")
    top = cur.fetchall()
    print(f"\n  Total leads          : {total:,}")
    print(f"  Available (pool)     : {avail:,}   (never distributed OR >= 7d old)")
    print(f"  Distributed (locked) : {total - avail:,}")
    print("  Top states:")
    for st, c in top:
        print(f"    {st or 'UNKNOWN':<10} {c:>10,}")


def main():
    ap = argparse.ArgumentParser(description="Postgres global-combine + state/agent distribution",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--by-state", action="store_true")
    ap.add_argument("--out", default=str(EXPORT_DIR / "by_state"))
    ap.add_argument("--redistribute", help="comma-separated agent names")
    ap.add_argument("--states", help="comma-separated state filter for --redistribute (e.g. TX,FL)")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--daily", action="store_true", help="--append then --by-state")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    day = date.today().isoformat()

    if args.dry_run:
        print("  [dry-run] would, against the leads model:")
        if args.append or args.daily:
            print("   - APPEND new businesses -> leads (state via derive_state, flow=global_combine)")
        if args.by_state or args.daily:
            print(f"   - BY-STATE export available_leads -> {args.out}/<STATE>.csv")
        if args.redistribute:
            print(f"   - REDISTRIBUTE pool -> agents {args.redistribute} in 10k chunks, mark distributed")
        if args.stats:
            print("   - STATS summary")
        print("\n  APPEND SQL:" + APPEND_SQL)
        return
    if not args.dsn:
        print("ERROR: no DSN. Set DATABASE_URL or pass --dsn.", file=sys.stderr); sys.exit(2)

    conn = get_conn(args.dsn)
    try:
        if args.append or args.daily:
            with conn.cursor() as cur:
                added = do_append(cur); conn.commit()
            print(f"  Global sheet: +{added:,} new phones appended to leads.")
        if args.by_state or args.daily:
            with conn.cursor() as cur:
                counts = do_by_state(cur, Path(args.out))
            print(f"  By-state: wrote {len(counts)} files to {args.out}  ({sum(counts.values()):,} rows)")
        if args.redistribute:
            agents = [a.strip() for a in args.redistribute.split(",") if a.strip()]
            states_filter = {s.strip().upper() for s in (args.states or "").split(",") if s.strip()}
            do_redistribute(conn, agents, states_filter, day)
        if args.stats:
            with conn.cursor() as cur:
                do_stats(cur)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
