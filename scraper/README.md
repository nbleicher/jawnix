# scale/ — scaling project for the Google Maps scraper

Everything for scaling the scraper to **250k net-unique records/day** lives here, separate from `auto_scrape/` (your actual scraper repo, left untouched). This folder holds the plans, the new control-plane + worker tooling, and the proposed code changes to apply into `auto_scrape/` when ready.

## Read in this order
1. **`GO_LIVE.md`** — the ordered execution checklist (start here); maps each step to the issue it closes.
2. **`PRD.md`** — the product requirements: goals, open issues, database plan, decisions (D1–D7), roadmap.
3. **`SCALING_PLAN.md`** — the higher-level scaling narrative.
4. **`SETUP_POSTGRES_CONTROL_VPS.md`** — stand up managed Postgres + the control VPS (do first).
5. **`CONTROL_VPS_PLAN.md`** — what the control VPS is and every utility it runs.
6. **`FIRST_BOT_SETUP.md`** — bring up box1 (the 18-core worker) and calibrate yield.
7. **`SPOOL_AND_SHIP.md`** — decoupled results loading (workers → local files → shipper → Postgres).
8. **`LEADS_POSTGRES_MODEL.md`** / **`PROXY_OPTIONS.md`** — lead data model (main.py → Postgres) and proxy choice.

## What's here
```
PRD.md, SCALING_PLAN.md, CONTROL_VPS_PLAN.md,
FIRST_BOT_SETUP.md, SETUP_POSTGRES_CONTROL_VPS.md   ← plans/runbooks

control/        runs on the CONTROL VPS (+ push from laptop)
  push_keywords.py    push keywords.txt laptop → control VPS (run locally)
  enqueue.py          continuous top-up job producer (self-contained)
  grid.py             vendored state bboxes + cell generator
  active_states.yaml  the ONE file you edit to change coverage
  export_leads.py     global-combine: businesses → leads + daily 10k CSVs
  alert.py            drift alerts (queue/pace/empty-rate) → webhook or exit-code
  bootstrap.sh        one-shot control VPS provisioning

worker/         runs on box1 + future worker fleet
  docker-compose.yml  12-container worker setup (+ /data/incoming spool volume)
  .env.example        DATABASE_URL / HASHID_SALT / PROXIES / GMS_SPOOL_DIR ...
  Dockerfile.saas     AUTHENTIC upstream worker image (pulled from gosom main)
  shipper.py          spool→Postgres loader (drains *.ndjson.done → businesses)
  bootstrap.sh        one-script box provision (deps, image, .env, event-driven shipper units)

util/
  monitor.py          live "total phones scraped" dashboard
  organize.py         per-keyword/state/date analysis folders (current -web data)

scraper_changes/      edits + upstream files to apply INTO auto_scrape/util/scraper_src
  REPO_SETUP.md                        own your fork + complete it (read first)
  migrations/…businesses_and_leads.sql the new schema
  centralwriter_businesses_change.md   the O-5 + FR-4.4 Go change (apply-ready patch)
  docker-compose.saas.yaml             authentic upstream local-dev Postgres (reference)
```

## Relationship to `auto_scrape/`
`auto_scrape/util/scraper_src` **is already your fork** of the gosom **SaaS edition** (the full `cmd/gmapssaas` + River queue + admin + provisioning — all open-source upstream), with your tweaks. It was just missing two upstream build files and isn't version-controlled. **You don't need to re-fork from scratch.** `scraper_changes/REPO_SETUP.md` shows how to own it in place, restore the two upstream files (`Dockerfile.saas` is in `worker/`; `docker-compose.saas.yaml` here), and apply the migration + Go change.

## Run notes (because tools now live in scale/, not in the scraper repo)
- **`util/monitor.py`** reads the fleet from `config.yaml`. Point it at the real one:
  `python3 util/monitor.py --watch --config /path/to/auto_scrape/config.yaml`
- **`control/push_keywords.py`** runs on your laptop. Give it the control host via flags or a `control:` block:
  `python3 control/push_keywords.py keywords.txt --host <control-vps> --trigger-run`
- **`control/enqueue.py`** is self-contained (grid logic vendored in `grid.py`). `--dry-run` works offline; real inserts need the `serve` API up.
- **`control/export_leads.py`** needs `DATABASE_URL` + the `businesses`/`leads` tables.

## Status
Plans + tooling complete. Pending external steps: choose proxies (D2), provision managed Postgres + control VPS, apply `scraper_changes/`, then bring up box1 and measure per-container yield (the number that sizes the fleet).
