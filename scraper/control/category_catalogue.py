"""Approved categories scheduled by state and untouched cells, without generation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_ALIASES = json.loads((Path(__file__).parent / "catalogue/aliases.json").read_text())
_NAMES = {
    name: group["category"]
    for group in _ALIASES
    for name in [group["category"], *group["aliases"]]
}
_GROUPS = {
    group["category"]: [group["category"], *group["aliases"]] for group in _ALIASES
}
_GENERIC = {
    "company",
    "companies",
    "contractor",
    "contractors",
    "service",
    "services",
    "specialist",
    "specialists",
}
_BLOCKED = re.compile(
    r"\b(attorneys?|lawyers?|law|legal|paralegals?|litigation|solicitors?|barristers?|notary|notaries)\b",
    re.IGNORECASE,
)
_SHAPE = re.compile(r"^[a-z0-9][a-z0-9 &'/-]*[a-z0-9]$|^[a-z0-9]{2,}$")


def normalized(value):
    return " ".join(value.strip().casefold().split())


def _signature(value):
    tokens = []
    for word in re.findall(r"[a-z0-9]+", value.replace("&", " and ")):
        if word in _GENERIC or word == "and":
            continue
        if word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            word = word[:-1]
        tokens.append(word)
    return " ".join(tokens) or value


_SIGNATURE_NAMES = {_signature(alias): name for alias, name in _NAMES.items()}


def category_identity(term):
    name = normalized(term)
    canonical = _NAMES.get(name, _SIGNATURE_NAMES.get(_signature(name), name))
    return hashlib.sha256(_signature(canonical).encode()).hexdigest(), canonical


def sync_catalogue(ledger, terms, started_on, *, origin):
    """Add approved/history-backed categories. An existing identity/query stays stable."""
    terms = sorted(
        {normalized(term) for term in terms}, key=lambda term: (len(term), term)
    )
    terms = [
        term
        for term in terms
        if 2 <= len(term) <= 60
        and 1 <= len(term.split()) <= 6
        and _SHAPE.fullmatch(term)
        and not _BLOCKED.search(term)
    ]
    with ledger.conn, ledger.conn.cursor() as cur:
        cur.execute(
            "SELECT alias FROM acquisition_category_aliases WHERE alias=ANY(%s)",
            (terms,),
        )
        known = {row[0] for row in cur.fetchall()}
        fresh = [term for term in terms if term not in known]
        if not fresh:
            return 0
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('acquisition-category-catalogue',0))"
        )
        alias_rows = {}
        category_rows = {}
        for term in fresh:
            category_id, canonical = category_identity(term)
            category_rows.setdefault(
                category_id, (category_id, canonical, started_on, origin)
            )
            for alias in [term, canonical, *_GROUPS.get(canonical, [])]:
                alias_rows[normalized(alias)] = (
                    normalized(alias),
                    category_id,
                    origin,
                )
        cur.executemany(
            "INSERT INTO acquisition_categories(id,canonical_query,started_on,origin) VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            list(category_rows.values()),
        )
        # If a normalized phrase already belongs to another category, keep its
        # previous reviewed membership instead of silently moving its coverage.
        cur.executemany(
            "INSERT INTO acquisition_category_aliases(alias,category_id,origin) VALUES(%s,%s,%s) ON CONFLICT(alias) DO NOTHING",
            list(alias_rows.values()),
        )
        cur.execute(
            """INSERT INTO acquisition_category_cells(category_id,state,cell,enqueued_at)
            SELECT a.category_id,lower(l.state),l.cell,coalesce(min(l.updated_at),now())
            FROM enqueue_log l JOIN acquisition_category_aliases a ON a.alias=lower(l.keyword)
            WHERE l.status='posted' AND a.alias=ANY(%s) AND l.state ~* '^[a-z]{2}$'
            GROUP BY a.category_id,lower(l.state),l.cell ON CONFLICT DO NOTHING""",
            (list(alias_rows),),
        )
    return len(fresh)


def import_history(ledger, started_on):
    """Prior configured campaigns carry provenance; business-label guesses do not."""
    with ledger.conn.cursor() as cur:
        cur.execute(
            "SELECT keyword FROM keyword_history UNION SELECT keyword FROM approved_campaign_keywords"
        )
        terms = [row[0] for row in cur.fetchall()]
    return sync_catalogue(ledger, terms, started_on, origin="historical_campaign")


_CANDIDATES = """SELECT c.id,c.canonical_query,c.started_on
FROM acquisition_categories c
WHERE c.enabled
 AND (%s OR EXISTS(SELECT 1 FROM acquisition_category_states started WHERE started.category_id=c.id AND started.state=%s))
 AND NOT EXISTS (SELECT 1 FROM acquisition_category_aliases a JOIN keyword_history h
    ON lower(h.keyword)=a.alias WHERE a.category_id=c.id AND h.state=%s)
 AND NOT EXISTS (SELECT 1 FROM acquisition_category_aliases a JOIN keyword_yield_evaluations e
    ON lower(e.keyword)=a.alias WHERE a.category_id=c.id AND e.state=%s
      AND (e.status IN ('rejected','completed','unverified','legacy') OR
           (e.status='accepted' AND e.policy IS NULL) OR
           (e.status IN ('testing','expanding') AND lower(e.keyword)<>c.canonical_query)))
ORDER BY EXISTS(SELECT 1 FROM keyword_yield_evaluations e WHERE e.keyword=c.canonical_query AND e.state=%s AND e.status='expanding') DESC,
 c.created_at,c.canonical_query LIMIT %s"""


def prepare(ledger, cfg, today, build_jobs, gate, *, limit=75, allow_new=True):
    jobs = []
    expected = {}
    counts = {}
    for state in cfg["states"]:
        state = state.lower()
        with ledger.conn.cursor() as cur:
            cur.execute(_CANDIDATES, (allow_new, state, state, state, state, limit))
            categories = cur.fetchall()
        counts[state] = len(categories)
        for category_id, term, started_on in categories:
            full = build_jobs({**cfg, "states": [state]}, [term])
            by_cell = {job["geo_coordinates"]: job for job in full}
            with ledger.conn.cursor() as cur:
                cur.execute(
                    "SELECT cells,started_on FROM acquisition_category_states WHERE category_id=%s AND state=%s",
                    (category_id, state),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "SELECT cell FROM acquisition_category_cells WHERE category_id=%s AND state=%s",
                        (category_id, state),
                    )
                    covered = {row[0] for row in cur.fetchall()}
                    cells = [cell for cell in by_cell if cell not in covered]
                    cur.execute(
                        "INSERT INTO acquisition_category_states(category_id,state,started_on,cells) VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                        (category_id, state, started_on, json.dumps(cells)),
                    )
                    cur.execute(
                        "SELECT cells,started_on FROM acquisition_category_states WHERE category_id=%s AND state=%s",
                        (category_id, state),
                    )
                    row = cur.fetchone()
                cells, started_on = row
            if not cells:
                continue
            # Keep the original eligible grid through pilot/wave evaluation;
            # removing newly posted cells would change membership on restart.
            full = [by_cell[cell] for cell in cells if cell in by_cell]
            expected[(term, state)] = len(cells)
            eligible = gate(
                ledger, full, started_on, today, cfg["queue"], allow_legacy=False
            )
            with ledger.conn.cursor() as cur:
                cur.execute(
                    "SELECT cell FROM acquisition_category_cells WHERE category_id=%s AND state=%s",
                    (category_id, state),
                )
                covered = {row[0] for row in cur.fetchall()}
            jobs.extend(
                job for job in eligible if job["geo_coordinates"] not in covered
            )
    return jobs, expected, counts
