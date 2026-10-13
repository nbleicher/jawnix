"""Retain approved campaigns and refill each active state's work independently."""
from __future__ import annotations

import hashlib
from collections import deque
from datetime import date

PENDING = """
SELECT b.keyword,b.started_on,COALESCE(e.status,'new'),COALESCE(e.new_phones_per_minute,0)
FROM state_campaign_backlog b
LEFT JOIN keyword_yield_evaluations e
 ON e.keyword=b.keyword AND e.state=b.state AND e.started_on=b.started_on
WHERE b.state=%s
 AND COALESCE(e.status,'new') NOT IN ('rejected','completed','unverified')
 AND NOT EXISTS (SELECT 1 FROM keyword_history h WHERE h.keyword=b.keyword AND h.state=b.state
                 AND h.last_enqueued>=b.started_on)
ORDER BY (e.status='expanding') DESC NULLS LAST,
         COALESCE(e.new_phones_per_minute,0) DESC,b.created_at,b.keyword
LIMIT %s
"""


def register(ledger, keywords, states, started_on: date, version=None):
    """Only caller-approved keywords enter this durable reserve; conflicts retain provenance."""
    keywords = list(dict.fromkeys(value.strip() for value in keywords if value.strip()))
    version = version or hashlib.sha256('\n'.join(keywords).encode()).hexdigest()
    with ledger.conn.cursor() as cur:
        cur.executemany("""INSERT INTO approved_campaign_keywords(keyword,started_on,approved_version)
            VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""", [(kw,started_on,version) for kw in keywords])
        # A state added to coverage receives the previously approved reserve too.
        for state in states:
            cur.execute("""INSERT INTO state_campaign_backlog(keyword,state,started_on)
                SELECT keyword,%s,started_on FROM approved_campaign_keywords
                ON CONFLICT DO NOTHING""", (state.lower(),))


def pending(ledger, states, limit=75):
    result={}
    with ledger.conn.cursor() as cur:
        for state in states:
            cur.execute(PENDING,(state.lower(),limit))
            result[state.lower()]=cur.fetchall()
    return result


def order_states(jobs, states, weights=None):
    """Preserve each state's pilot/wave order and borrow slots when a state has no work."""
    states=list(dict.fromkeys(state.lower() for state in states))
    queues={state:deque(job for job in jobs if job['state']==state) for state in states}
    weights=weights or {state:1 for state in states}
    credits={state:0 for state in states}
    ordered=[]
    while any(queues.values()):
        live=[state for state in states if queues[state]]
        total=sum(weights.get(state,1) for state in live)
        for state in live:credits[state]+=weights.get(state,1)
        chosen=max(live,key=lambda state:credits[state])
        credits[chosen]-=total
        ordered.append(queues[chosen].popleft())
    return ordered


def prepare(ledger, cfg, keywords, started_on, today, build_jobs, gate, weights=None):
    states=cfg['states']
    register(ledger,keywords,states,started_on)
    selected=pending(ledger,states)
    jobs=[]
    expected={}
    # Each campaign keeps the date and immutable policy of its first approval.
    for state, campaigns in selected.items():
        by_day={}
        for keyword,day,_,_ in campaigns:by_day.setdefault(day,[]).append(keyword)
        for day,terms in by_day.items():
            full=build_jobs({**cfg,'states':[state]},terms)
            cells={}
            for job in full:cells.setdefault((job['keyword'],state),set()).add(job['geo_coordinates'])
            expected.update({pair:len(value) for pair,value in cells.items()})
            candidates=gate(ledger,full,day,today,cfg['queue'])
            with ledger.conn.cursor() as cur:
                cur.execute("""SELECT keyword,state,cell FROM enqueue_log
                    WHERE day>=%s AND state=%s AND
                    (status='posted' OR (status='reserved' AND updated_at>=now()-interval '15 minutes'))""",(day,state))
                done=set(cur.fetchall())
            jobs.extend(job for job in candidates if (job['keyword'],state,job['geo_coordinates']) not in done)
    return order_states(jobs,states,weights),expected,{state:len(campaigns) for state,campaigns in selected.items()}
