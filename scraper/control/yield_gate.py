"""Durable campaign pilots. Infrastructure failures never reject a keyword."""
from __future__ import annotations

import json
import math
import time

SAMPLE_SIZE = 6
MIN_PRODUCTIVE_CELLS = 2
MIN_PHONES = 3
_scores_cache: dict = {}


def sample_cells(cells: list[str], scores: dict[str, int]) -> list[str]:
    """Prefer productive cells, with geographic spread among productive choices."""
    remaining = sorted(cells, key=lambda cell: (-scores.get(cell, 0), cell))
    selected = []
    while remaining and len(selected) < SAMPLE_SIZE:
        candidates = [cell for cell in remaining if scores.get(cell, 0) > 0] or remaining
        if not selected:
            best = candidates[0]
        else:
            def priority(cell):
                lat, lon = map(float, cell.split(","))
                separation = min(
                    math.hypot(lat - float(other.split(",")[0]),
                               (lon - float(other.split(",")[1])) * math.cos(math.radians(lat)))
                    for other in selected
                )
                return min(separation, 0.5), scores.get(cell, 0), cell
            best = max(candidates, key=priority)
        selected.append(best)
        remaining.remove(best)
    return selected


def decide(completed: int, productive: int, phones: int, sample_count: int) -> str:
    if productive >= min(MIN_PRODUCTIVE_CELLS, sample_count) and phones >= MIN_PHONES:
        return "accepted"
    if completed >= sample_count:
        return "rejected"
    return "testing"


def eligible_jobs(ledger, jobs: list[dict], started_on, today) -> list[dict]:
    pairs = {}
    for job in jobs:
        pairs.setdefault((job["keyword"], job["state"]), []).append(job)
    pilots, expanded = [], []
    with ledger.conn.cursor() as cur:
        cur.execute("SELECT blocked_until IS NOT NULL FROM acquisition_health WHERE id")
        if cur.fetchone()[0]:
            return []
        # Reuse scores across watch iterations. Query only recent, already indexed results.
        cache_key = ledger.conn.dsn
        if cache_key not in _scores_cache or time.monotonic() - _scores_cache[cache_key][0] > 300:
            cur.execute("""SELECT lower(j.args->>'state'), j.args->>'geo_coordinates', sum(sr.phone_count)
                FROM scrape_results sr JOIN river_job j ON j.id=sr.job_id
                WHERE sr.created_at>=now()-interval '24 hours'
                GROUP BY 1,2""")
            scores = {(state, cell): int(phones) for state, cell, phones in cur.fetchall()}
            _scores_cache[cache_key] = (time.monotonic(), scores)
        scores = _scores_cache[cache_key][1]
        for (keyword, state), group in pairs.items():
            cur.execute("""SELECT sample_cells,status FROM keyword_yield_evaluations
                WHERE keyword=%s AND state=%s AND started_on=%s""", (keyword, state, started_on))
            row = cur.fetchone()
            if row is None:
                # A campaign started before rollout may already have statewide coverage in flight.
                cur.execute("""SELECT EXISTS(SELECT 1 FROM enqueue_log WHERE keyword=%s AND state=%s
                    AND day>=%s AND status='posted')""", (keyword,state,started_on))
                legacy = cur.fetchone()[0]
                cells = sample_cells([job["geo_coordinates"] for job in group],
                                     {cell: value for (st, cell), value in scores.items() if st == state})
                status = "legacy" if legacy else "testing"
                cur.execute("""INSERT INTO keyword_yield_evaluations
                    (keyword,state,started_on,sample_cells,full_cells,status)
                    VALUES (%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING""",
                    (keyword,state,started_on,json.dumps(cells),len(group),status))
                cur.execute("""SELECT sample_cells,status FROM keyword_yield_evaluations
                    WHERE keyword=%s AND state=%s AND started_on=%s""", (keyword,state,started_on))
                cells, status = cur.fetchone()
            else:
                cells, status = row
            if status == "testing":
                # Only the final classified success/empty outcome per cell is usable evidence.
                cur.execute("""WITH latest AS (
                    SELECT DISTINCT ON (cell) cell,outcome,phone_count FROM scrape_attempts
                    WHERE keyword=%s AND state=%s AND cell=ANY(%s) AND created_at>=%s::date
                    ORDER BY cell,created_at DESC,attempt DESC)
                    SELECT count(*) FILTER (WHERE outcome IN ('success','empty')),
                           count(*) FILTER (WHERE outcome='success' AND phone_count>0),
                           COALESCE(sum(phone_count) FILTER (WHERE outcome='success'),0)
                    FROM latest""", (keyword,state,cells,started_on))
                completed, productive, phones = map(int,cur.fetchone())
                status = decide(completed, productive, phones, len(cells))
                # Exhausted transport failures leave the keyword unjudged, but must not stall rollover.
                if status == "testing":
                    cur.execute("""SELECT count(DISTINCT args->>'geo_coordinates') FROM river_job
                        WHERE kind='scrape' AND args->>'keyword'=%s AND args->>'state'=%s
                          AND args->>'geo_coordinates'=ANY(%s) AND state IN ('discarded','cancelled')
                          AND created_at>=%s::date""", (keyword,state,cells,started_on))
                    if completed + int(cur.fetchone()[0]) >= len(cells):
                        status = "unverified"
                        reason = "Pilot incomplete after bounded acquisition retries. Yield unverified."
                    else:
                        reason = None
                else:
                    reason = "Insufficient phone yield in pilot." if status == "rejected" else None
                cur.execute("""UPDATE keyword_yield_evaluations SET status=%s,completed_cells=%s,
                    productive_cells=%s,phone_count=%s,reason=%s,updated_at=now()
                    WHERE keyword=%s AND state=%s AND started_on=%s""",
                    (status,completed,productive,phones,reason,keyword,state,started_on))
            if status == "testing":
                pilots.append([job for job in group if job["geo_coordinates"] in cells])
            elif status in ("accepted", "legacy"):
                expanded.extend(sorted(group, key=lambda job: -scores.get((state,job["geo_coordinates"]),0)))
    # Fairly distribute pilot cells across candidates before expanded campaigns.
    ordered = [group[index] for index in range(SAMPLE_SIZE) for group in pilots if index < len(group)]
    return ordered + expanded
