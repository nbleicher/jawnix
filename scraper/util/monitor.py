#!/usr/bin/env python3
"""
monitor.py — live progress dashboard for the scraper fleet
==========================================================
A terminal UI that shows, across every VPS instance:
  • TOTAL phone numbers scraped so far  (counted from the live CSVs on each box)
  • per-state and per-instance breakdown
  • job progress (done / running / queued / failed)
  • live scrape rate (phones/hour) while watching

It reads config.yaml (same file orchestrate.py uses) and works against the
current `-web` fleet — no changes to the boxes required. Counts come straight
from each instance's data folder, so the totals are real, not estimates.

Usage:
  python3 monitor.py                 # one-shot snapshot
  python3 monitor.py --watch         # live, refresh every 30s
  python3 monitor.py --watch 10      # live, refresh every 10s
  python3 monitor.py --vps vps1      # only one VPS
  python3 monitor.py --by-state      # collapse instances, show state totals only
"""

import argparse
import concurrent.futures
import json
import os
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

import paramiko
import yaml

HERE = Path(__file__).parent
CONFIG_FILE = HERE.parent / "config.yaml"


# ── SSH helpers (same pattern as stats.py) ────────────────────────────────────
def ssh_connect(host, user, password, port=22):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=user, password=password, timeout=15)
    return client


def ssh_run(client, cmd, timeout=20):
    _, stdout, _ = client.exec_command(cmd, timeout=timeout)
    return stdout.read().decode().strip()


def get_job_stats(client, port: int) -> dict:
    script = shlex.quote(
        "import json\n"
        "from urllib.request import urlopen\n"
        "try:\n"
        f"    resp = urlopen('http://localhost:{port}/api/v1/jobs', timeout=10)\n"
        "    jobs = json.load(resp) or []\n"
        "    c = {}\n"
        "    for j in jobs:\n"
        "        s = j.get('Status','unknown'); c[s] = c.get(s,0)+1\n"
        "    print(json.dumps(c))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    out = ssh_run(client, f"python3 -c {script}", timeout=15)
    try:
        return json.loads(out)
    except Exception:
        return {"error": out or "no response"}


def get_csv_rows(client, data_folder: str) -> int:
    """Count data rows (minus headers) across all CSVs in the data folder."""
    script = shlex.quote(
        "import glob\n"
        "t = 0\n"
        f"for f in glob.glob('{data_folder}/**/*.csv', recursive=True):\n"
        "    try:\n"
        "        with open(f) as fh: t += max(0, sum(1 for _ in fh) - 1)\n"
        "    except Exception: pass\n"
        "print(t)\n"
    )
    out = ssh_run(client, f"python3 -c {script}", timeout=20)
    try:
        return int(out.strip())
    except Exception:
        return -1


def check_vps(vps: dict) -> dict:
    name = vps["name"]
    try:
        client = ssh_connect(vps["host"], vps["user"], vps["password"], vps.get("ssh_port", 22))
    except Exception as e:
        return {"vps": name, "error": str(e), "instances": []}
    home = ssh_run(client, "echo $HOME", timeout=5).strip() or "/root"
    instances = []
    for inst in vps["instances"]:
        counts = get_job_stats(client, inst["port"])
        data_folder = inst["data_folder"].replace("~", home)
        rows = get_csv_rows(client, data_folder)
        instances.append({
            "screen": inst["screen_name"],
            "state": inst["state"].upper(),
            "port": inst["port"],
            "counts": counts,
            "rows": rows,
        })
    client.close()
    return {"vps": name, "instances": instances}


# ── Snapshot collection (VPS machines polled in parallel) ─────────────────────
def collect(vps_list: list) -> list:
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(vps_list))) as pool:
        futs = {pool.submit(check_vps, v): v["name"] for v in vps_list}
        for fut in concurrent.futures.as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"vps": futs[fut], "error": str(e), "instances": []})
    return sorted(results, key=lambda r: r["vps"])


def fmt_int(n):
    return f"{n:,}" if isinstance(n, int) and n >= 0 else "?"


# ── Rendering ─────────────────────────────────────────────────────────────────
def render(results: list, by_state: bool, prev: dict, t_prev: float):
    """Return (text, snapshot) where snapshot maps a key→total phones for rate calc."""
    lines = []
    now = datetime.now()
    lines.append("=" * 72)
    lines.append(f"  SCRAPER FLEET MONITOR        {now:%Y-%m-%d %H:%M:%S}")
    lines.append("=" * 72)

    grand = {"done": 0, "running": 0, "queued": 0, "failed": 0, "phones": 0}
    state_phones: dict = {}

    for r in results:
        if r.get("error"):
            lines.append(f"\n  {r['vps']:8s}  ⚠ ERROR: {r['error']}")
            continue
        lines.append(f"\n  {r['vps']}")
        if not by_state:
            lines.append(f"    {'inst':<9} {'st':<3} {'phones':>9}  "
                         f"{'done':>6} {'run':>4} {'queued':>7} {'fail':>5}  prog")
        for inst in r["instances"]:
            c = inst["counts"]
            st = inst["state"]
            rows = inst.get("rows", -1)
            if rows >= 0:
                state_phones[st] = state_phones.get(st, 0) + rows
                grand["phones"] += rows
            if "error" in c:
                if not by_state:
                    lines.append(f"    {inst['screen']:<9} {st:<3} {fmt_int(rows):>9}  "
                                 f"⚠ {c['error'][:30]}")
                continue
            done = c.get("ok", 0); run = c.get("working", 0)
            queued = c.get("pending", 0); fail = c.get("failed", 0)
            tot = done + run + queued + fail
            pct = f"{done/tot*100:.0f}%" if tot else "—"
            grand["done"] += done; grand["running"] += run
            grand["queued"] += queued; grand["failed"] += fail
            if not by_state:
                lines.append(f"    {inst['screen']:<9} {st:<3} {fmt_int(rows):>9}  "
                             f"{done:>6,} {run:>4} {queued:>7,} {fail:>5}  {pct:>4}")

    # Per-state rollup
    if state_phones:
        lines.append("\n  ── phones by state ──")
        row = "   "
        for i, (st, n) in enumerate(sorted(state_phones.items(), key=lambda x: -x[1])):
            row += f" {st}:{n:>7,}"
            if (i + 1) % 5 == 0:
                lines.append(row); row = "   "
        if row.strip():
            lines.append(row)

    # Rate (phones/hour) vs previous snapshot
    rate_str = ""
    if prev and t_prev:
        dt = time.time() - t_prev
        dn = grand["phones"] - prev.get("phones", grand["phones"])
        if dt > 0 and dn >= 0:
            per_hr = dn / dt * 3600
            rate_str = f"   (+{dn:,} since last refresh → {per_hr:,.0f}/hr)"

    lines.append("\n" + "-" * 72)
    lines.append(f"  TOTAL PHONES SCRAPED: {grand['phones']:>12,}{rate_str}")
    lines.append(f"  jobs  done={grand['done']:,}  running={grand['running']}  "
                 f"queued={grand['queued']:,}  failed={grand['failed']}")
    lines.append("-" * 72)
    return "\n".join(lines), {"phones": grand["phones"]}


def main():
    ap = argparse.ArgumentParser(description="Live progress monitor for the scraper fleet")
    ap.add_argument("--watch", nargs="?", const=30, type=int, default=None,
                    metavar="SECS", help="Live refresh every SECS seconds (default 30)")
    ap.add_argument("--vps", metavar="NAME", help="Only this VPS")
    ap.add_argument("--by-state", action="store_true", help="Collapse instances; show state totals")
    ap.add_argument("--config", default=str(CONFIG_FILE))
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    vps_list = cfg["vps_list"]
    if args.vps:
        vps_list = [v for v in vps_list if v["name"] == args.vps]
        if not vps_list:
            print(f"No VPS named '{args.vps}'"); sys.exit(1)

    if args.watch is None:
        results = collect(vps_list)
        text, _ = render(results, args.by_state, {}, 0)
        print(text)
        return

    prev, t_prev = {}, 0.0
    try:
        while True:
            results = collect(vps_list)
            text, snap = render(results, args.by_state, prev, t_prev)
            os.system("clear" if os.name != "nt" else "cls")
            print(text)
            print(f"\n  refreshing every {args.watch}s — Ctrl-C to quit")
            prev, t_prev = snap, time.time()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
