"""Measure canonical new endpoints and expand campaigns in bounded waves."""
from __future__ import annotations

import json
import math
import time

SAMPLE_SIZE = 6
MIN_PRODUCTIVE_CELLS = 2
MIN_PHONES = 3
_scores_cache: dict = {}
DEFAULT_POLICY = {
    "pilot_cells": 6, "wave_cells": 12, "min_new_phones": 3,
    "min_productive_cells": 2, "wave_min_new_phones": 3,
    "wave_min_productive_cells": 2, "max_cells": 0,
}


def policy_from_queue(queue):
    policy = {key:int(queue.get("yield_"+key, value)) for key,value in DEFAULT_POLICY.items()}
    if policy["pilot_cells"]<2 or policy["wave_cells"]<2:
        raise ValueError("Pilot and wave sizes must be at least two")
    for size, required in [("pilot_cells","min_productive_cells"),("wave_cells","wave_min_productive_cells")]:
        if not 1<=policy[required]<=policy[size]:
            raise ValueError("Productive cell threshold exceeds sample size")
    if policy["min_new_phones"]<1 or policy["wave_min_new_phones"]<1:
        raise ValueError("New phone thresholds must be positive")
    if policy["max_cells"] and policy["max_cells"]<policy["pilot_cells"]:
        raise ValueError("Coverage cap cannot be smaller than the pilot")
    return policy


def distance(cell, other):
    lat, lon = map(float, cell.split(","))
    other_lat, other_lon = map(float, other.split(","))
    return math.hypot(lat-other_lat,(lon-other_lon)*math.cos(math.radians(lat)))


def sample_cells(cells, scores, size=SAMPLE_SIZE):
    remaining = sorted(cells, key=lambda cell: (-scores.get(cell,0),cell))
    selected = []
    while remaining and len(selected)<size:
        candidates = [cell for cell in remaining if scores.get(cell,0)>0] or remaining
        best = candidates[0] if not selected else max(candidates,key=lambda cell:(
            min(min(distance(cell,other) for other in selected),0.5),scores.get(cell,0),cell))
        selected.append(best);remaining.remove(best)
    return selected


def decide(completed, productive, phones, sample_count):
    if completed<sample_count:
        return "testing"
    return "accepted" if productive>=min(MIN_PRODUCTIVE_CELLS,sample_count) and phones>=MIN_PHONES else "rejected"


def evidence(cur, keyword, state, cells, started_on):
    cur.execute("""WITH latest AS (
        SELECT DISTINCT ON (cell) job_id,cell,outcome FROM scrape_attempts
        WHERE keyword=%s AND state=%s AND cell=ANY(%s) AND created_at>=%s::date
        ORDER BY cell,created_at DESC,attempt DESC)
        SELECT l.cell,l.outcome,sr.yield_verified,sr.new_phone_count,sr.phone_count,
               COALESCE((SELECT sum(duration_ms) FROM scrape_attempts a WHERE a.job_id=l.job_id),0),
               j.state FROM latest l LEFT JOIN scrape_results sr ON sr.job_id=l.job_id
               LEFT JOIN river_job j ON j.id=l.job_id""",(keyword,state,cells,started_on))
    rows=cur.fetchall()
    verified=[row for row in rows if row[1] in ("success","empty") and row[2]]
    productive=[row[0] for row in verified if row[3]>0]
    new=sum(row[3] for row in verified)
    duration=sum(row[5] for row in rows)
    exhausted=sum(row[1] not in ("success","empty") and row[6] in ("discarded","cancelled") for row in rows)
    return {"completed":len(verified),"productive":productive,"new":new,
            "raw":sum(row[4] for row in verified),"rate":60_000*new/max(duration,1000),"exhausted":exhausted}


def cell_scores(ledger, cur):
    key=ledger.conn.dsn
    if key not in _scores_cache or time.monotonic()-_scores_cache[key][0]>300:
        cur.execute("""WITH attempts AS (
            SELECT job_id,max(state) AS state,max(cell) AS cell,sum(duration_ms) AS duration_ms
            FROM scrape_attempts WHERE created_at>=now()-interval '24 hours' GROUP BY job_id
        ) SELECT lower(a.state),a.cell,
            CASE WHEN sum(sr.new_phone_count)>0
                 THEN 60000.0*sum(sr.new_phone_count)/greatest(sum(a.duration_ms),1000)
                 ELSE 0 END,
            sum(sr.phone_count) FROM scrape_results sr
            JOIN attempts a ON a.job_id=sr.job_id
            WHERE sr.created_at>=now()-interval '24 hours' GROUP BY 1,2""")
        rows=cur.fetchall()
        # Fresh new-phone efficiency wins. Existing density is only a bootstrap tie-breaker.
        scores={(state,cell):float(rate)+min(int(raw),100000)*1e-9 for state,cell,rate,raw in rows}
        if not scores:
            cur.execute("""SELECT lower(j.args->>'state'),j.args->>'geo_coordinates',sum(sr.phone_count)
                FROM scrape_results sr JOIN river_job j ON j.id=sr.job_id
                WHERE sr.created_at>=now()-interval '24 hours' GROUP BY 1,2""")
            scores={(state,cell):min(int(raw),100000)*1e-9 for state,cell,raw in cur.fetchall()}
        _scores_cache[key]=(time.monotonic(),scores)
    return _scores_cache[key][1]


def eligible_jobs(ledger, jobs, started_on, today, queue=None):
    pairs={}
    for job in jobs:pairs.setdefault((job["keyword"],job["state"]),[]).append(job)
    pilots,expanded=[],[]
    with ledger.conn.cursor() as cur:
        cur.execute("SELECT blocked_until IS NOT NULL FROM acquisition_health WHERE id")
        if cur.fetchone()[0]:return []
        scores=cell_scores(ledger,cur)
        for (keyword,state),group in pairs.items():
            grid=[job["geo_coordinates"] for job in group]
            cur.execute("""SELECT sample_cells,status,coverage_cells,wave_cells,policy,new_phones_per_minute
                FROM keyword_yield_evaluations WHERE keyword=%s AND state=%s AND started_on=%s""",(keyword,state,started_on))
            row=cur.fetchone()
            if row is None:
                cur.execute("SELECT EXISTS(SELECT 1 FROM enqueue_log WHERE keyword=%s AND state=%s AND day>=%s AND status='posted')",
                            (keyword,state,started_on))
                legacy=cur.fetchone()[0]
                policy=policy_from_queue(queue or {})
                cells=sample_cells(grid,{cell:value for (st,cell),value in scores.items() if st==state},policy["pilot_cells"])
                status="legacy" if legacy else "testing"
                coverage=grid if legacy else cells
                cur.execute("""INSERT INTO keyword_yield_evaluations
                    (keyword,state,started_on,sample_cells,full_cells,status,coverage_cells,wave_cells,policy)
                    VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (keyword,state,started_on,json.dumps(cells),len(grid),status,json.dumps(coverage),json.dumps(cells),json.dumps(policy)))
                cur.execute("""SELECT sample_cells,status,coverage_cells,wave_cells,policy,new_phones_per_minute
                    FROM keyword_yield_evaluations WHERE keyword=%s AND state=%s AND started_on=%s""",(keyword,state,started_on))
                row=cur.fetchone()
            cells,status,coverage,wave,policy,rate=row
            if status in ("legacy","accepted") and policy is None:
                expanded.append((0,group));continue
            if status=="legacy":
                expanded.append((0,group));continue
            if not policy: # Old rejected/unverified evaluation remains terminal.
                continue
            coverage=coverage or cells;wave=wave or cells
            if status in ("testing","expanding"):
                current=evidence(cur,keyword,state,wave,started_on)
                cumulative=evidence(cur,keyword,state,coverage,started_on)
                reason=None
                if current["completed"]>=len(wave):
                    pilot=status=="testing"
                    minimum=policy["min_new_phones"] if pilot else policy["wave_min_new_phones"]
                    productive=policy["min_productive_cells"] if pilot else policy["wave_min_productive_cells"]
                    passed=current["new"]>=minimum and len(current["productive"])>=min(productive,len(wave))
                    limit=min(len(grid),policy["max_cells"] or len(grid))
                    remaining=[cell for cell in grid if cell not in coverage]
                    if not passed:
                        status="rejected" if pilot else "completed"
                        reason="Pilot lacked new unique phones." if pilot else "Stopped expansion after low new-phone yield."
                    elif not remaining or len(coverage)>=limit:
                        status="completed";reason="Coverage complete." if not remaining else "Operator coverage cap reached."
                    else:
                        anchors=cumulative["productive"]
                        remaining.sort(key=lambda cell:(-scores.get((state,cell),0),min(distance(cell,p) for p in anchors),cell))
                        wave=remaining[:min(policy["wave_cells"],limit-len(coverage))]
                        coverage=[*coverage,*wave];status="expanding"
                elif current["completed"]+current["exhausted"]>=len(wave):
                    status="unverified";reason="Wave could not be verified after bounded acquisition retries."
                rate=cumulative["rate"]
                cur.execute("""UPDATE keyword_yield_evaluations SET status=%s,coverage_cells=%s::jsonb,wave_cells=%s::jsonb,
                    completed_cells=%s,productive_cells=%s,phone_count=%s,new_phone_count=%s,new_phones_per_minute=%s,
                    reason=%s,updated_at=now() WHERE keyword=%s AND state=%s AND started_on=%s""",
                    (status,json.dumps(coverage),json.dumps(wave),cumulative["completed"],len(cumulative["productive"]),
                     cumulative["raw"],cumulative["new"],rate,reason,keyword,state,started_on))
            if status=="testing":pilots.append([job for job in group if job["geo_coordinates"] in coverage])
            elif status=="expanding":expanded.append((rate,[job for job in group if job["geo_coordinates"] in coverage]))
            elif status=="completed" and len(coverage)==len(grid):
                # Preserve the precise meaning of historical full statewide coverage.
                cur.execute("""INSERT INTO keyword_history(keyword,state,last_enqueued) VALUES (%s,%s,%s)
                    ON CONFLICT(keyword,state) DO UPDATE SET last_enqueued=EXCLUDED.last_enqueued""",(keyword,state,today))
    ordered=[group[index] for index in range(max((len(g) for g in pilots),default=0)) for group in pilots if index<len(group)]
    return ordered+[job for _,group in sorted(expanded,key=lambda item:-item[0]) for job in group]
