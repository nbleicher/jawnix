# Leads data model — main.py → Postgres

Your `main.py` defines how you want lead data organized: a phone-keyed master ("manifest"), **state derived from the phone's area code**, per-state grouping, a 7-day re-distribution pool, and 10k-row agent files. This maps it 1:1 onto Postgres so the queue-era pipeline produces the same shape — as queries instead of files.

**Implemented by:** migration `scraper_changes/migrations/20260619000000-leads-distribution-model.sql` (area-code seed + `derive_state()` + `leads` columns + `available_leads` view) and `control/export_leads.py`.

## Concept mapping

| main.py | Postgres |
|---|---|
| `manifest.csv` (every phone ever seen) | **`leads`** table (`phone` PK) |
| manifest fields `phone,title,state,first_seen,flow,agent,date_distributed` | `leads` columns (+ `business_id` link to the scraped record) |
| `derive_state(phone)` via `AREA_CODE_STATE` (419 codes) | `derive_state(phone)` SQL function + `area_codes` lookup table |
| dedup by phone (`scrub`) | `INSERT … ON CONFLICT (phone) DO NOTHING` |
| `REDISTRIBUTION_DAYS = 7` eligibility | **`available_leads`** view (never distributed OR ≥7d old) |
| `all_combined.csv` (the pool) | `SELECT * FROM available_leads` |
| `build_state_files` → `by_state/XX.csv` | `GROUP BY state` / per-state `COPY` (`export_leads.py --by-state`) |
| Global Combine (option 2) | `export_leads.py --append` |
| Redistribute (option 3), 10k chunks, mark agent+date | `export_leads.py --redistribute A,B` |
| Stats / Age Distribution | `--stats` / SQL on `date_distributed` |
| Update Unknown (`unknown_states.csv`) | `UPDATE leads … WHERE state='UNKNOWN'` + add codes to `area_codes` |
| `internal_clean` (sacred master, never distributed) | `flow IN ('init_internal','internal')` — exclude from `available_leads` if desired |

## `leads` (the manifest)
```
phone            TEXT PRIMARY KEY     -- normalized 10-digit (the global dedup key)
title            TEXT
state            TEXT                 -- derive_state(phone): area-code tag (AL…WY, DC, PR/VI/GU, CAN, INTL, TOLL-FREE, UNKNOWN)
first_seen       DATE
flow             TEXT                 -- provenance: 'global_combine', 'internal', 'history', …
agent            TEXT                 -- current assignment (NULL = undistributed)
date_distributed DATE                 -- NULL = never
business_id      BIGINT → businesses  -- link back to the scraped record
```

## State derivation (area code, not scrape state)
`leads.state` comes from the **phone's area code** — same as `main.py` — via `derive_state()` over the seeded `area_codes` table. (The scrape-side `businesses.state` records *where it was scraped*; the lead-side `leads.state` records the *phone's* state, which is what your distribution uses.)

## Canonical queries (what each main.py flow becomes)

**Global Combine — append new phones (state from area code):**
```sql
INSERT INTO leads (phone, title, state, flow, business_id)
SELECT DISTINCT ON (regexp_replace(b.phone,'\D','','g'))
       regexp_replace(b.phone,'\D','','g'), b.title, derive_state(b.phone), 'global_combine', b.id
FROM businesses b
WHERE length(regexp_replace(b.phone,'\D','','g')) = 10
ON CONFLICT (phone) DO NOTHING;
```

**By-state pool counts / export:**
```sql
SELECT state, count(*) FROM available_leads GROUP BY state ORDER BY 2 DESC;
-- per state:  COPY (SELECT phone,title FROM available_leads WHERE state='TX') TO STDOUT CSV HEADER;
```

**Redistribute to an agent (10k handled by the chunker), then lock:**
```sql
UPDATE leads SET agent='alice', date_distributed=CURRENT_DATE WHERE phone = ANY(:assigned);
```

**Re-distribution eligibility (the 7-day rule)** is the `available_leads` view; a distributed phone re-enters the pool automatically 7 days later.

**Fix UNKNOWN states** (the `unknown_states.csv` flow): add the missing code(s) to `area_codes`, then
```sql
UPDATE leads SET state = derive_state(phone) WHERE state = 'UNKNOWN';
```

## How scraped data flows in
worker → `businesses` (deduped place, with phone) → `export_leads.py --append` → `leads` (deduped phone, area-code state) → `--by-state` / `--redistribute` → 10k CSVs. New scraped phones join the manifest automatically; everything else (state grouping, 7-day pool, agent files) is a query.

## Apply order
After `businesses`/`leads` (20260617) and the control ledger (20260618), run this migration (20260619). All three live in `scraper_changes/migrations/` and are applied together per `SETUP_POSTGRES_CONTROL_VPS.md` Part A4.

> **Open choice:** whether `internal_clean` (your sacred master that's never distributed) is modeled as `flow='internal'` rows excluded from `available_leads`, or a separate table. Tell me which and I'll wire it into the view.
