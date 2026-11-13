# PRD — Google Maps Lead-Scraping Platform at Scale

| | |
|---|---|
| **Document** | Product Requirements Document (PRD) |
| **Project** | `auto_scrape` — distributed Google Maps scraping platform |
| **Owner** | Noah Bleicher |
| **Status** | Draft v6 |
| **Last updated** | 2026-06-17 |
| **Target** | Reliably collect **250,000 net-unique records/day** across a changing set of US states |

> **Repository layout:** all deliverables (this PRD, the runbooks, and the `control/`, `worker/`, `util/` tooling) now live in the **`scale/`** folder. `auto_scrape/` is the untouched scraper repo. Proposed edits to that repo (the `businesses`/`leads` migration and the worker Go change) live in **`scale/scraper_changes/`** and are applied into `auto_scrape/` when ready. Paths below are relative to `scale/` unless prefixed `auto_scrape/`.

**Changelog**
- **v10 (2026-06-18):** **Decision:** spool-and-ship (Part C) is the chosen result path (not direct-write). Docs/runbooks updated to make it canonical; Part A demoted to a skipped reference. Worker writes NDJSON; `shipper.py` loads `businesses`.
- **v9 (2026-06-18):** Added **spool-and-ship** (`SPOOL_AND_SHIP.md`): workers write NDJSON + `.done` markers to a local spool; `worker/shipper.py` (event-driven via a systemd `.path`, + backstop timer) bulk-loads to `businesses`. Decouples scrape throughput from DB latency and uses one DB connection per box (largely mitigates O-9). Go patch gains Part C (spool variant) as the recommended fleet path.
- **v8 (2026-06-18):** Modeled `main.py`'s lead conventions in Postgres — full `leads` manifest (area-code `state` via `derive_state()` + seeded `area_codes`, 419 codes), `available_leads` 7-day pool view, and `export_leads.py` `--append/--by-state/--redistribute/--stats`. Migration `20260619…-leads-distribution-model.sql`; mapping in `LEADS_POSTGRES_MODEL.md`.
- **v7 (2026-06-18):** Built **O-10/O-11/O-12**: `alert.py` + `gms-alert.timer` (drift alerts), and a Postgres ledger (`enqueue_log` + `keyword_history`, migration `…control-ledger.sql`) wired into `enqueue.py` for idempotent enqueue + campaign dedup (`--skip-recent-days`). All pending deploy.
- **v6 (2026-06-17):** Confirmed the gosom **SaaS edition is open-source upstream** — `scraper_src` is already your fork of it, just missing two build files. Pulled the **authentic `Dockerfile.saas`** (browser downloaded at runtime by playwright-go — no version pinning) and `docker-compose.saas.yaml` (dev Postgres) from upstream `main`. Added `scraper_changes/REPO_SETUP.md` (own the fork in place rather than re-cloning). The worker-image blocker is cleared.
- **v5 (2026-06-17):** Relocated everything to `scale/`. Corrected the O-5 resolution to the **additive** change actually delivered — keep `scrape_results` (`rqueue.go` reads it) and *also* upsert `businesses` from `centralwriter.go`; apply-ready patch in `scale/scraper_changes/centralwriter_businesses_change.md`. **FR-4.4** (`state`/`cell` provenance) implemented as Part B of that patch. Created the `businesses`/`leads` migration in `scale/scraper_changes/migrations/`. Fixed an `enqueue.py` field bug (`depth`→`max_depth`).
- **v4 (2026-06-17):** Recorded decisions D1–D7. Added real throughput measurement (50k phones / 16h on 7 instances). **Resolved O-5** (single deduplicated table written directly — §12.3). Added the cumulative "global-combine" lead layer + daily 10k exports (FR-4.6), keyword-push tooling (FR-6.4), and the live progress monitor `util/monitor.py` (FR-7.5).
- **v3 (2026-06-16):** Trimmed §3 to **open issues only** — anything already fixed, mitigated, or with a settled resolution was removed (it lives in the requirements/plan, not the issue list). Replaced §12 with a concrete **Database Plan** (engine, hosting recommendation, schema/DDL, dedup key, connection pooling, sizing, migrations, retention, backups).
- **v2 (2026-06-16):** Added consolidated, severity-rated issues register; noted the keyword-loss defect in `clean_csv.py` and the `util/organize.py` mitigation.
- **v1 (2026-06-16):** Initial PRD — goals, target architecture, FR/NFR, data model, capacity, roadmap, acceptance criteria.

---

## 1. Summary

The platform scrapes Google Maps business listings (name, address, phone, website, category, coordinates, reviews, optionally email) for lead generation. The work is organized by US state: each state's bounding box is divided into a grid of cells, and every `(keyword × cell)` pair becomes a scrape job.

Today the system runs in a simple but throughput-limited mode (one process per state on manually-managed VPSes, SQLite per instance, a daily SSH-driven restart cycle). This PRD specifies the re-architecture to a **central job queue + horizontally-scaled stateless worker fleet** — a design that **already exists in the codebase** (`rqueue/`, `cmd/gmapssaas/cmdworker`, `postgres/`, `infra/`) but is not yet operationalized. The goal is to reach 250k/day reliably, make the active-state list a trivial config change, and remove the operator's laptop as a single point of failure.

This document defines the goals, requirements, the full issues/concerns register, data model, capacity plan, reliability behavior, rollout milestones, and acceptance criteria for that buildout.

---

## 2. Background & current state

### 2.1 As-is architecture
- **Control plane:** `orchestrate.py` on the operator's Mac. Daily it kills all `screen` sessions over SSH, SFTP-pulls each instance's CSV, restarts instances, expands `keywords × grid cells`, and POSTs jobs to each instance's local REST API. `clean_csv.py` merges/splits output; `keyword_db.py` (SQLite) tracks used keyword/state pairs; a cron "stuck-job watcher" deletes frozen jobs.
- **Data plane:** Go fork of `gosom/google-maps-scraper` run in `-web` mode — one process per state, own port, own SQLite `jobs.db`, own CSV folder. Current fleet: 2 VPS / 8 instances (OH, TX, NC, SC, IN, TN, FL, UT), `-c 20` tabs each.
- **Measured throughput (real):** **50,000 phone numbers in ~16 hours across 7 instances** → ~75k/day extrapolated, **~10.7k/instance/day** (gross, not deduped). Confirms the upper-mid of the earlier 5–15k estimate.

### 2.2 Latent capability (built, unused)
The repo already contains: a **River job queue** on Postgres (`rqueue/`), a **stateless worker** (`cmd/.../cmdworker`) that pulls jobs and writes results straight to Postgres (`scraper/centralwriter.go`), **VPS auto-provisioning** + cloud-init (`infra/vps`, `infra/cloudinit`) for Hetzner/DigitalOcean, an **admin dashboard** (`admin/`) with jobs/workers/auth/2FA, a **record deduper** (`deduper/`), and alternative execution backends (S3, AWS Lambda).

---

## 3. Open Issues (unresolved)

Only issues with **no resolution in place** are listed here. Anything already fixed, mitigated, or with a settled approach (e.g. the keyword-loss handled by `util/organize.py`, the concurrency=1 trap now specced as container-scaling, the browser/Docker and timeout fixes) has been removed — those live in the requirements and plan, not here. Each item is either a **gap to build**, an **open decision**, or an **unmeasured unknown**.

**Severity:** 🔴 Critical · 🟠 High · 🟡 Medium

### 3.1 Critical

| ID | Open issue | Sev | What's needed |
|---|---|---|---|
| O-1 | **Secrets are still live in git + history.** Root password (`Trixie12`) and `local/test_ssh_key` are committed. Until rotated and purged from history, the fleet is compromised by anyone with repo access. | 🔴 | Rotate password, move to key-based SSH, `git filter-repo` to purge history. **Do first.** |
| O-2 | **No proxy solution.** Scraping runs from raw VPS IPs; vendor/plan not chosen. This is the true throughput ceiling and is currently unaddressed. | 🔴 | Pick residential/rotating vendor; A/B one proxied vs. raw worker (decision **D2**). |
| O-3 | **Per-container yield is unmeasured.** The whole fleet-sizing model swings ~3× on this number (600 vs. 2,000 net-unique/container/day) and we have no real measurement yet. | 🔴 | Measure on one box in M1 before buying any fleet. Gates everything downstream. |

### 3.2 High

| ID | Open issue | Sev | What's needed |
|---|---|---|---|
| O-4 | **Record-level dedup does not exist.** `scrape_results` stores one JSONB blob per *job*, not per business, so "net-unique/day" (K1) is currently uncomputable. No `businesses` table exists. | 🟠 | Build the deduped `businesses` table (dedup key **decided, D5**); worker writes deduped rows directly (§12.3). |
| O-6 | **State is still coupled to infrastructure.** No active-state-list config exists yet; adding/removing a state still means editing `config.yaml` and restarting. | 🟠 | Build the config-driven enqueuer (FR-1.1, FR-5.3). |
| O-7 | **Control plane still runs on the laptop (SPOF).** Nothing runs if the Mac is offline; no always-on host is provisioned. | 🟠 | Stand up an always-on control host (FR-6.1). Decision **D4**. |
| O-8 | **Postgres hosting is undecided and becomes the new single point of failure.** No DB is provisioned; HA/backup posture unset. | 🟠 | Choose host + tier + backup/PITR (see §12, decision **D1**). |

### 3.3 Medium

| ID | Open issue | Sev | What's needed |
|---|---|---|---|
| O-9 | **Connection saturation at fleet scale.** `3 conns/worker × ~400 containers` ≫ default Postgres `max_connections`. | 🟡 | ✅ **Largely mitigated** by spool-and-ship (`SPOOL_AND_SHIP.md`) — **one DB connection per box** (the shipper) instead of per container. PgBouncer optional after that. |
| O-10 | **No monitoring / alerting.** Can't answer "are we on pace?" without SSH-ing into boxes. | 🟡 | ✅ **Built** — `scale/control/alert.py` + `gms-alert.timer` (queue/pace/empty-rate/reachability → webhook or exit-code). Pending deploy. |
| O-11 | **Enqueue idempotency & backpressure not built.** Re-runs can duplicate pending jobs; full expansion can flood the queue with millions of rows. | 🟡 | ✅ **Built** — backpressure via `target_depth`; idempotency via the `enqueue_log` ledger (`scale/scraper_changes/migrations/…control-ledger.sql`) wired into `enqueue.py`. Pending deploy. |
| O-12 | **Campaign dedup (`keyword_history`) is local SQLite.** A distributed control plane can't share it. | 🟡 | ✅ **Built** — Postgres `keyword_history` (same migration); `enqueue.py --skip-recent-days` / `queue.skip_recent_days`. Pending deploy. |
| O-13 | **Retention / partitioning undecided.** `scrape_results` grows by potentially hundreds of thousands of job rows/day with JSONB blobs; no retention or partitioning plan. | 🟡 | Decide retention window + partition strategy (§12, decision **D6**). |

### 3.4 Standing (no fix — to be managed)

Google Maps anti-bot/DOM changes (pin version, monitor K4/K5), legal/ToS exposure of large-scale scraping (operator risk acceptance + data policy), and cost trajectory (proxy spend likely ≥ VPS; track K8). These have no "resolution" by nature and are tracked as risks in §16.

> *O-5 (missing `results` migration) was resolved in v4 — see §12.2. Numbering is left unchanged so prior references stay valid.*

**Do-first order:** O-1 (secrets) → O-3 (measure yield) → O-2 (proxies) → O-8/O-4 (database + dedup) → O-6/O-7 (decouple state, move control plane) → O-9–O-13.

---

## 4. Goals & non-goals

### 4.1 Goals
- **G1.** Sustain **250,000 net-unique records/day**, with a reliability buffer so a single box/region/proxy outage does not drop output below target.
- **G2.** Make the **active state list a config-only change** — add or drop a state without touching infrastructure.
- **G3.** **Self-healing throughput:** worker or job failures are detected and recovered automatically, without a daily manual restart.
- **G4.** **Operate without the operator's laptop** — control plane runs on always-on infrastructure.
- **G5.** **Record-level dedup** so reported volume reflects unique businesses.
- **G6.** **Observability:** real-time visibility into queue depth, worker health, throughput, and block rate.
- **G7.** **Secure by default** — no plaintext secrets, key-based access, least privilege.

### 4.2 Non-goals (this phase)
- Building a customer-facing SaaS product or billing (the `admin`/API SaaS scaffolding is used internally only).
- Scraping sources other than Google Maps.
- Real-time/streaming scraping (batch daily cycle is acceptable).
- ML enrichment of leads (downstream concern).

---

## 5. Success metrics (KPIs)

| ID | Metric | Target | Measurement |
|---|---|---|---|
| K1 | Net-unique records/day | ≥ 250,000 | Distinct rows added to the `businesses` table/day (see FR-4.1) |
| K2 | Daily target hit-rate | ≥ 95% of days | Rolling 30-day |
| K3 | Worker utilization | ≥ 80% | `active_jobs ÷ container_count` across fleet |
| K4 | Job success rate | ≥ 90% | Completed ÷ (completed + permanently-failed) |
| K5 | Block/empty-result rate | ≤ 10% | Jobs returning 0 results ÷ total |
| K6 | Mean time to recover a dead worker's jobs | ≤ 20 min | River rescue interval |
| K7 | Time to add/remove a state | ≤ 5 min, no redeploy | Operator action |
| K8 | Cost per 1,000 net-unique records | Track & trend down | (VPS + proxy spend) ÷ K1 |

---

## 6. Personas

- **Operator (primary, Noah):** runs daily cycles, sets the active state list and keywords, watches dashboards, exports leads.
- **Maintainer:** deploys/updates the worker image, manages the queue/DB, tunes capacity.
- **Downstream consumer:** receives exported lead files/queries for outreach.

---

## 7. Target architecture

```
   ┌──────────────────────────────────────────────┐
   │ Control plane (always-on host)                │
   │  - Enqueuer: reads active-state list +        │
   │    keywords → expands grid → inserts jobs      │
   │  - Scheduler (daily/continuous)                │
   │  - Admin dashboard + API (built)               │
   │  - Exporter (Postgres → CSV/handoff)           │
   └───────────────┬───────────────────────────────┘
                   │ InsertJob (QueueDefault)
                   ▼
   ┌──────────────────────────────────────────────┐
   │ Managed PostgreSQL                            │
   │  - River queue (river_job, leasing, retries)  │
   │  - scrape_results (per-job) → businesses (dedup)│
   │  - rate_limits, app_config, admin/provisioning│
   └───────────────┬───────────────────────────────┘
        pull jobs   │   batched upserts (50/flush)
        ┌───────────┼───────────┬───────────────┐
        ▼           ▼           ▼               ▼
   worker box 1  worker box 2  …            worker box N
   (18c/64GB: ~12 worker containers each, 1 job/container,
    each behind a rotating proxy, /health on :8080)
```

**Key property:** workers are identical and disposable; a state is just a tag on the jobs you enqueue. Scaling = add worker boxes/containers. Changing states = change what the enqueuer inserts.

---

## 8. Functional requirements

### 8.1 Job production / Enqueuer
- **FR-1.1** Maintain an **active-state list** as versioned config (e.g. `active_states.yaml`): per state, the 2-letter code, bbox, and cell size (defaults already encoded in `STATE_CONFIG`).  *(Resolves O-6)*
- **FR-1.2** Expand each active state into grid cells (reuse `generate_grid_cells`, which mirrors `grid/grid.go`) and produce one `ScrapeJobArgs` per `(keyword × cell)`.
- **FR-1.3** Insert jobs into the River **`QueueDefault`** via the `serve` API `POST /api/v1/scrape` (→ `Client.InsertJob`). Each job carries: keyword, lang, geo_coordinates (`lat,lon`), zoom, radius, depth, fast_mode, extra_reviews, timeout.
- **FR-1.4** **Keyword/campaign de-duplication in Postgres:** before enqueuing, filter out `(keyword, state)` pairs already enqueued within a configurable look-back window. **Implemented** — Postgres `keyword_history` + `enqueue.py --skip-recent-days`. *(Resolves O-12)*
- **FR-1.5** **Backpressure:** cap enqueue so queue depth stays within a target band rather than dumping millions of rows at once. Top up as depth drains. **Implemented** — `enqueue.py` `target_depth` top-up. *(Resolves O-11)*
- **FR-1.6** **Idempotent enqueue:** re-running the enqueuer must not create duplicate pending jobs for the same `(keyword, cell, day)`. **Implemented** — the `enqueue_log` ledger (UNIQUE on keyword,state,cell,day). *(Resolves O-11)*
- **FR-1.7** Run on a **schedule** (daily and/or continuous top-up) from the control-plane host, not the operator laptop.

### 8.2 Queue (River on Postgres)
- **FR-2.1** Use the existing River config: scrape jobs on `QueueDefault`; maintenance jobs on `QueueMaintenance`.
- **FR-2.2** Honor `MaxAttempts = 3` for scrape jobs with retry/backoff; the retry promoter is enabled in workers.
- **FR-2.3** **Stuck-job rescue:** `RescueStuckJobsAfter = 20m` re-queues jobs from dead workers automatically (replaces the SQLite stuck-job DELETE hack). Tune and monitor this interval (K6).
- **FR-2.4** **Per-job timeout** is capped at `maxScrapeTimeout = 5m` (`JobTimeout = 5m + 2m`). The enqueuer must **not** carry the legacy 96h `timeout_secs`.
- **FR-2.5** Queue metrics (available/running/completed/retryable/discarded counts) must be queryable for dashboards and alerts. *(Supports O-9, O-10)*

### 8.3 Worker fleet
- **FR-3.1** Workers run as the **Docker image** via `docker compose`, scaled with `--scale worker_replica=N` (cloud-init / `GenerateUpdateCommand` already produce this). Running the bare binary is **not** supported (browser deps / `shm` requirements).
- **FR-3.2** Each worker container processes **one** scrape job at a time (by design). **Concurrency = container count.** On an 18-vCPU/64GB box, run ~**12 containers** (each `1.5` cpu / `2g` / `shm 1g`).
- **FR-3.3** Workers consume `QueueDefault`, write results via the batched writer (flush every 50 rows or on completion), and expose **`/health` on :8080** reporting `active_jobs`, `results_per_minute`, `jobs_processed`, and watchdog counters.
- **FR-3.4** **Proxy support:** each worker accepts a `PROXIES` env (comma-separated). Proxies are required before scaling concurrency (see §11). *(Resolves O-2)*
- **FR-3.5** `MAX_JOBS_PER_CYCLE` (default 100) restarts the embedded scraper periodically to bound memory/browser leaks; tunable per box.
- **FR-3.6** Workers are **stateless** — losing a worker loses no data; in-flight jobs are rescued (FR-2.3).
- **FR-3.7** A worker whose `/health` reports `active_jobs = 0` while queue depth > 0, or `results_per_minute = 0` over a threshold, must be flagged unhealthy. *(Supports O-10)*

### 8.4 Data store, dedup & export
> **Current behavior (spool-and-ship):** the worker writes raw per-business NDJSON files plus `.done` markers to the worker-host spool. `worker/shipper.py` drains those files into Postgres. Raw payload provenance lives in the archived NDJSON files, not in a large per-job JSONB table.
- **FR-4.1** Add a **normalized, deduplicated `businesses` table** populated from the workers' NDJSON spool, with a **unique key** on Google `place_id`/CID (preferred) or a normalized `name+address+phone` hash (the `deduper/` package provides hashing). Inserts use `ON CONFLICT … DO NOTHING/UPDATE`. K1 is measured against this table. **Delivered:** migration `scale/scraper_changes/migrations/20260617000000-add_businesses_and_leads.sql`; `businesses` is populated by `shipper.py` (spool-and-ship, §12.3). *(Resolves O-4, O-5)*
- **FR-4.2** Keep a lightweight per-job `scrape_results` row with `job_id`, `keyword`, `result_count`, and `created_at` for queue stats and empty-rate alerts. Do not store the raw JSONB blob in this path; archived NDJSON is the raw audit source.
- **FR-4.3** **Export on demand:** generate CSV (and the existing 10k-row split format) via a Postgres query/export job — replacing SFTP pulls + `clean_csv.py` in the daily path.
- **FR-4.4** Track per-record provenance natively: **state, keyword, cell, source_job_id, scraped_at**. Removes the dependence on parsing `input_id` and makes per-keyword/state analysis a plain `WHERE`. **Implemented** as Part B of `scale/scraper_changes/centralwriter_businesses_change.md` (threads `state`/`cell` from the enqueuer → API → job → writer); `enqueue.py` already sends `state`.
- **FR-4.5** Configurable **retention** for any raw audit table and completed `river_job` rows.
- **FR-4.6** **Global-combine lead layer + daily exports** (the `main.py` model). A daily job appends new, phone-deduplicated leads from `businesses` into a cumulative `leads` table (the "global sheet"; never re-emits a phone) and writes that day's new leads as **10k-row CSVs**. Port `main.py`'s area-code→state mapping and chunker. See §12.5.

> **Interim (current `-web` pipeline):** `util/organize.py` already provides per-keyword analysis folders today by reading the raw CSVs **before** `clean_csv.py` runs — it parses the keyword from `input_id`, derives state from the path and date from the folder, dedups on `place_id → cid → title+phone`, and writes `{state}_{keyword}_{date}/` folders + a date-level index. (Now complemented by FR-4.4 native provenance on `businesses` for the queue-era data.)

### 8.5 Provisioning & scaling
- **FR-5.1** Use `infra/vps` + `infra/cloudinit` to bring a worker box from bare VPS to running fleet via cloud-init (Docker install, `.env`, compose, `--scale`). SSH hardened to port 2222, pubkey-only.
- **FR-5.2** Support **Hetzner and DigitalOcean** (both implemented) so the fleet spans ≥2 providers/regions for reliability.
- **FR-5.3** **One-command capacity change:** adjust container count on a box (`GenerateUpdateCommand`) or add a box, without touching the queue or enqueuer. *(Resolves O-6)*
- **FR-5.4** (Stretch) **Auto-scale:** a maintenance job adjusts worker count to keep queue depth in band and utilization ≥ K3.
- **FR-5.5** Worker health-check + auto-delete maintenance jobs (`WorkerHealthCheckWorker`, `WorkerDeleteWorker`) reap dead boxes.

### 8.6 Control plane & orchestration
- **FR-6.1** Enqueuer, scheduler, API/serve, and exporter run on an **always-on host** (small dedicated VPS or the admin box), not the laptop (G4). *(Resolves O-7)*
- **FR-6.2** Daily cycle becomes: top-up enqueue → fleet drains queue continuously → export → repeat. No kill/restart of workers required.
- **FR-6.3** All control-plane actions are logged and re-runnable (idempotent).
- **FR-6.4** **Keyword push from local.** A local command (`push_keywords.py`) uploads an updated `keywords.txt` from the operator's machine to the control VPS over SSH/SCP, validates it, atomically replaces the active list, and optionally triggers the next enqueue top-up. The control VPS is the single source of the active keyword list; the laptop only pushes. (D1/D4.)

### 8.7 Observability
- **FR-7.1** **Admin dashboard** (`admin/`) shows jobs, workers, and throughput; reachable over authenticated HTTPS with 2FA (already built). *(Resolves O-10)*
- **FR-7.2** Aggregate fleet `/health` into a single throughput + utilization view (records/min, active jobs, per-box status).
- **FR-7.3** **Alerts** on: daily pace behind target (K2), queue depth out of band, block/empty rate > K5, worker unhealthy (FR-3.7), DB connection saturation. **Implemented** — `scale/control/alert.py` (`gms-alert.timer`, every 10 min; webhook or non-zero exit). *(Resolves O-10)*
- **FR-7.4** Daily run summary (records collected, unique added, jobs done/failed, per-state breakdown).
- **FR-7.5** **Live progress monitor** showing **total phone numbers scraped**, per-state and per-instance breakdown, job progress, and live scrape rate (phones/hour). Delivered now as `util/monitor.py` against the current `-web` fleet (counts CSV rows on each box via SSH; `--watch` for a refreshing UI). Post-migration it reads the same totals from `businesses`/`leads`.

### 8.8 Security
- **FR-8.1** **No secrets in git.** Rotate the committed root password; remove `local/test_ssh_key`; **purge both from history** (e.g. `git filter-repo`). Use env/secret files (`.env`, git-ignored) and a secrets store. *(Resolves O-1)*
- **FR-8.2** Key-based SSH only; per-box keys; no shared passwords. *(Resolves O-1)*
- **FR-8.3** Postgres reachable only by control plane + workers (private network/allowlist + TLS). *(Supports O-8, O-9)*
- **FR-8.4** Admin dashboard behind auth + 2FA + TLS; API keys scoped (`handlers_apikeys`).
- **FR-8.5** Encrypt provider/proxy credentials at rest (`cryptoext`, `app_config` encryption key already present).

---

## 9. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | Throughput | ≥ 250k net-unique/day sustained; architecture scales linearly by adding workers |
| NFR-2 | Reliability | No single box/region/proxy outage drops output below target (≥1.3× capacity buffer) |
| NFR-3 | Recoverability | Worker/job failures auto-recover ≤ 20 min; no manual daily restart |
| NFR-4 | Scalability | Add a worker box and have it draining jobs in ≤ 15 min via cloud-init |
| NFR-5 | Maintainability | Single worker image; config-driven states; infra-as-code provisioning |
| NFR-6 | Security | Per §8.8; no plaintext secrets anywhere |
| NFR-7 | Cost | Track $/1k records (K8); prefer cheaper provider mix where reliability allows |
| NFR-8 | Observability | Operator can answer "are we on pace?" in <1 min from the dashboard |

---

## 10. Capacity & cost model

**Planning assumptions** (calibrate in M1):
- Net-unique records / worker-container / day: **conservative 600–1,000** (one browser, 5-min cap, dedup, blocks). *This is the critical number to measure first.*
- Reliability buffer: **1.3×**.

> **Real measurement (current `-web` fleet):** 50k phones / 16h on 7 instances ≈ **10.7k/instance/day gross**. A `-web` instance runs ~20 tabs; a worker container runs 1 browser — so this implies very roughly **~500–800 gross/container/day** before proxies/dedup, consistent with the conservative end above. **This is gross (pre-phone-dedup); net-unique will be lower** — reinforcing why O-3 (measure deduped per-container yield on box1) gates the fleet count.

**Per box (18 vCPU / 64GB):** ~12 containers. At 800 net-unique/container/day → **~9,600/box/day**.

**Fleet sizing for 250k/day net-unique (×1.3 buffer ≈ 325k effective):**

| Per-container/day | Per box (12) | Boxes needed (incl. buffer) |
|---|---|---|
| 600 | 7,200 | ~45 → infeasible per box; tune first |
| 800 | 9,600 | ~34 boxes |
| 1,200 | 14,400 | ~23 boxes |
| 2,000 | 24,000 | ~14 boxes |

> The spread shows why **M1 calibration gates everything**: per-container yield drives box count by 3×. With proxies and tuned depth, 1,200–2,000/container is plausible; without proxies, blocking pushes you toward the bad end. **Do not buy the fleet before M1.**

**Cost lines:** VPS fleet (per-box monthly) + **proxy pool** (sized to request volume, likely comparable to or larger than VPS spend) + 1 managed Postgres + 1 control-plane host. Track K8.

---

## 11. Proxy strategy (reliability-critical)

- **P1.** Residential/rotating proxies strongly preferred over datacenter for Google Maps (far lower block rate).
- **P2.** Each worker routes through the pool via `PROXIES`; distribute IPs so no single IP exceeds Google's soft limits.
- **P3.** **M0 A/B test:** one proxied worker vs. one raw-IP worker; compare yield and empty-result rate. This decides spend and the per-container yield used in §10.
- **P4.** Monitor block/empty rate (K5) continuously; auto-rotate or cool down IPs that spike.

---

## 12. Database Plan

The database is the backbone of the re-architecture: it is the job queue, the result store, the dedup authority, and the source for all exports/analysis. It is also the new single point of failure (O-8), so hosting and pooling decisions matter.

### 12.1 Engine & hosting *(decision D1: managed Postgres)*
- **Engine:** **PostgreSQL 15+** — non-negotiable; River requires Postgres, and the existing migrations are Postgres-specific. Database name `gmaps_pro` (per `dbconfig.yml`).
- **Decided:** **managed Postgres** in the **same region as the worker fleet**, with automated daily backups + PITR and a standby. Start **~4 vCPU / 16 GB / 100 GB SSD**, scale vertically. **Do not co-locate the DB on a worker box** (browser workers are CPU/RAM-hungry).
- **Control plane host (decision D4):** a managed Postgres is a *service*, not a shell host, so the control plane can't literally live "on" it. Instead run **one small always-on control VPS** (1–2 vCPU) that connects to the managed DB and hosts the enqueuer, `serve` API, exporter, the global-combine job, and the monitor — and is the **push target for `keywords.txt`** (FR-6.4). It's all light I/O-bound work, so co-locating those functions on a single cheap box is fine. *(If you later self-host Postgres on a VPS instead, that same box can host the control plane too — both are light.)*

### 12.2 Connection pooling (resolves O-9)
Each worker opens up to `db_max_conns = 3`. At ~400 containers (34 boxes × 12) that's ~1,200 connections — well past Postgres's practical `max_connections` (~100–200).
- Put **PgBouncer in transaction-pooling mode** in front of the DB for the worker result-writes (short, bursty upserts) — collapses thousands of client conns onto a small server pool.
- Give **River its own small session-mode pool** (River uses LISTEN/NOTIFY + advisory locks that need session affinity); do **not** route River through transaction-mode PgBouncer.
- Also lower `db_max_conns` toward **1–2** per worker — each container runs one job at a time, so it needs very few.

### 12.3 Tables

**Managed / existing (already in `migrations/`):**
- **`river_job`** — the queue: state, attempts, leasing, retries, rescue. Managed by River; do not hand-edit.
- **`scrape_results`** — lightweight per-job counts: `job_id` (PK), `keyword`, `result_count`, `created_at`. Used by queue stats and empty-rate alerts. Raw payloads stay in archived NDJSON files.
- **`rate_limits`**, **`app_config`** (encrypted secrets via `cryptoext`), **admin/provisioning/2FA** tables.

**O-5 resolution (delivered — spool-and-ship, Part C chosen).** The worker's `centralwriter.go` `pgSave` writes each job's results as **NDJSON + a `.done` marker** to a local spool; `worker/shipper.py` bulk-loads them into **`businesses`** with `ON CONFLICT (dedup_key)` (dedup at load) and records a lightweight `scrape_results.result_count` row. This decouples scraping from DB latency and uses one DB connection per box. Apply-ready patch: **`scale/scraper_changes/centralwriter_businesses_change.md`** (Part B = `state`/`cell` provenance / FR-4.4, Part C = spool writer; Part A direct-write is the skipped alternative).

**To build:**

**`businesses`** — the normalized, deduplicated record store, written by `worker/shipper.py`. This is the table K1 ("net-unique/day") counts and the one analysis/exports read from.

```sql
-- +migrate Up
CREATE TABLE businesses (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dedup_key     TEXT NOT NULL UNIQUE,         -- place_id | cid | md5(norm name+phone+addr)
    place_id      TEXT,
    cid           TEXT,
    title         TEXT,
    phone         TEXT,
    website       TEXT,
    category      TEXT,
    address       TEXT,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    rating        DOUBLE PRECISION,
    review_count  INTEGER,
    emails        TEXT[],
    -- provenance (FR-4.4)
    state         TEXT,
    keyword       TEXT,
    cell          TEXT,
    source_job_id BIGINT,
    raw           JSONB,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX idx_businesses_state_keyword ON businesses (state, keyword);
CREATE INDEX idx_businesses_last_seen     ON businesses (last_seen);
CREATE INDEX idx_businesses_place_id      ON businesses (place_id);
-- +migrate Down
DROP TABLE businesses;
```

**`keyword_history`** *(migrate from the local SQLite, resolves O-12)* — campaign-level dedup:

```sql
-- +migrate Up
CREATE TABLE keyword_history (
    keyword      TEXT NOT NULL,
    state        TEXT NOT NULL,
    last_enqueued DATE NOT NULL,
    UNIQUE (keyword, state)
);
CREATE INDEX idx_keyword_history_state ON keyword_history (state);
-- +migrate Down
DROP TABLE keyword_history;
```

**O-5 (delivered):** resolved via spool-and-ship — the worker spools NDJSON and `shipper.py` loads `businesses` plus lightweight `scrape_results` counts (see §12.3). The heavy raw JSONB `scrape_results.results` path is not used.

### 12.4 Dedup key (decision D5)
- **Primary key = Google `place_id`** — Google's stable per-place identifier; the correct dedup key. Fall back to `cid`, then to `md5(normalized name + phone + address)` when neither ID is present.
- Implement as a single `dedup_key` column (`UNIQUE`) computed at ingest, so one `ON CONFLICT (dedup_key)` handles all three cases. This mirrors the logic already proven in `util/organize.py` (place_id → cid → title+phone).

### 12.5 Two-level dedup: businesses → leads (the "global combine" layer)
There are two distinct units, and they need two dedup keys:
- **Unique business** — keyed on `place_id` (§12.4). Written directly by the worker into `businesses`. Answers "what did we scrape?"
- **Unique phone (lead)** — your downstream (`main.py`) distributes *phone numbers* and dedups by phone. A business with no phone is not a lead; two businesses rarely share a phone. So the lead layer dedups on the **normalized 10-digit phone**.

This mirrors `main.py` exactly: `businesses` (unique places) feeds a **cumulative phone "global sheet"** that only ever grows and never re-emits a phone. Implement it as a `leads` table (the DB-native equivalent of `main.py`'s `manifest.csv`):

`leads` is the DB-native equivalent of `main.py`'s `manifest.csv` — the **full manifest shape** (phone, title, state, first_seen, flow, agent, date_distributed, business_id), with `state` from the **area code** via `derive_state()`, a seeded `area_codes` table, and an `available_leads` view implementing the 7-day re-distribution rule. Created in migration `20260617…` (base) + extended by `20260619…-leads-distribution-model.sql`. The full mapping (and the by-state / redistribute / stats queries) is in **`LEADS_POSTGRES_MODEL.md`**; the tool is `control/export_leads.py` (`--append/--by-state/--redistribute/--stats`).

**Daily global-combine job** (on the control host, FR-4.6): insert any new phones from `businesses` into `leads`, deduped, then emit that day's new leads as **10k-row CSVs**:

```sql
INSERT INTO leads (phone, title, state, business_id)
SELECT DISTINCT ON (regexp_replace(b.phone,'\D','','g'))
       regexp_replace(b.phone,'\D','','g') AS phone,
       b.title, b.state, b.id
FROM businesses b
WHERE b.phone <> '' AND length(regexp_replace(b.phone,'\D','','g')) = 10
ON CONFLICT (phone) DO NOTHING;            -- never re-emit a known phone
```

The "append to a global sheet once a day + create daily 10k CSVs" you described is then: this upsert (the append) plus a chunked `COPY ... (SELECT ... FROM leads WHERE first_seen = current_date)` to `lead_YYYY-MM-DD_N.csv` files of 10,000 rows (the daily exports). `main.py`'s area-code→state map and 10k chunker port over directly.

### 12.6 Sizing & growth
- **`businesses`:** ~0.5–1.5 KB/row with `raw`. Net-new rows shrink over time as coverage saturates (dedup rejects repeats). First months: low single-digit millions of rows → a few GB. Cheap.
- **`scrape_results`:** a lightweight count table — one row per *job* with count metadata only. The archived NDJSON spool is the raw audit storage driver.

### 12.7 Retention & partitioning (decision D6, resolves O-13)
- Keep archived raw NDJSON for the chosen audit window (default **30–90 days**) and prune it at the filesystem/archive layer.
- Keep `businesses` **indefinitely** (it's the asset). Completed `river_job` rows: let River's built-in cleaner prune them.

### 12.8 Migrations & ops
- Use the existing tooling: **`rubenv/sql-migrate`** with `-- +migrate Up/Down` files in `util/scraper_src/migrations/`, auto-run on startup via `migrations.Run`. Add the `businesses`, `keyword_history`, and `results`-resolution migrations there.
- Backups/PITR per §12.1; test a restore before M2.

---

## 13. Reliability & failure-mode handling

| Failure | Detection | Automatic response |
|---|---|---|
| Worker container crashes | River lease expiry | Job rescued after ≤20 min (FR-2.3); `restart: unless-stopped` relaunches container |
| Whole box dies | Health-check maintenance job | Jobs rescued; box reaped (FR-5.5); operator alerted |
| Browser hangs on a job | 5-min job timeout | Job fails, retried up to 3× (FR-2.2) |
| IP blocked / empty results | K5 block-rate monitor | Rotate/cool down proxy IP; alert if fleet-wide |
| Queue drained (idle workers) | Depth monitor + utilization | Enqueuer tops up (FR-1.5); alert if enqueuer stalled |
| Queue overfilled | Depth monitor | Backpressure halts enqueue (FR-1.5) |
| Postgres saturation | Conn/latency monitor | Cap worker `db_max_conns` (3/worker); scale DB |
| Control-plane host down | External uptime check | Alert; host is always-on, restart-on-failure |

---

## 14. Roadmap & milestones

| Milestone | Scope | Closes | Exit criteria |
|---|---|---|---|
| **M0 — Secure & measure (week 1)** | Rotate/purge secrets; SSH keys; stand up Postgres + run migrations; proxy A/B on 1 worker | O-1, O-3, O-2 (start) | Secrets out of git + history; DB live; **per-container yield + block rate measured** |
| **M1 — One box, done right (week 1–2)** | Deploy worker image + compose at ~12 containers on the 18-core box; run `serve` enqueuer feeding `QueueDefault`; verify `/health`; add `businesses` table, resolve `results` path, set up PgBouncer | O-5, O-8, O-9 | One box sustains measured net-unique/day; utilization ≥ K3; results deduped in Postgres |
| **M2 — Fleet out (week 2–4)** | Cloud-init provisioning; scale to calibrated box count across Hetzner + DO; active-state list as config; backpressure/idempotent enqueue | O-2, O-4, O-6, O-11 | ≥ 250k/day net-unique with 1.3× buffer; add/drop state in ≤5 min |
| **M3 — Self-running (week 4–6)** | Move control plane off laptop; scheduler; dashboard + alerts; Postgres exporter; record-level dedup + provenance hardened; migrate keyword_history; retention/partitioning | O-7, O-10, O-12, O-13 | Hits target ≥95% of days unattended; alerts fire correctly; exports on demand |
| **M4 — Optimize (ongoing)** | Auto-scaling (FR-5.4); cost tuning (K8); depth-banded enqueue | — (standing) | $/1k records trending down; utilization stable |

---

## 15. Acceptance criteria (definition of done)

- **AC1.** A single 18-core box running the worker image at ~12 containers produces a **measured, repeatable** net-unique/day figure, visible via aggregated `/health` and confirmed in the `businesses` table.
- **AC2.** Enqueuer inserts `(keyword × cell)` jobs for exactly the **active-state list**; changing that list changes output states next cycle with **no redeploy**.
- **AC3.** Killing a worker mid-job results in the job being **completed by another worker** within 20 min, with no duplicate row.
- **AC4.** Fleet sustains **≥250k net-unique/day on ≥95% of days** over a 30-day window.
- **AC5.** No plaintext secrets in the repo or history; all access key-based.
- **AC6.** Operator can answer "are we on pace today?" from the dashboard in under a minute, and receives an alert when behind.
- **AC7.** Every stored record carries state/keyword/cell/scraped_at provenance, and per-keyword exports are a single query (no `input_id` parsing).

---

## 16. Risks & mitigations (forward delivery)

*(The exhaustive issue catalog is §3; this is the delivery-risk view.)*

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| IP blocking caps throughput (no/insufficient proxies) | High | High | Residential rotating pool; M0 A/B; K5 monitoring; provider/region spread |
| Per-container yield lower than hoped → many boxes | Medium | High | M1 calibration before fleet buy; tune depth/fast_mode; right-size proxies |
| Re-introducing the "concurrency=1" mistake | Medium | High | FR-3.1/3.2 mandate container-scaling; runbook + dashboard check |
| Postgres becomes bottleneck at fleet scale (O-9) | Medium | Medium | PgBouncer; bounded conns/worker; managed/scaled DB; batch upserts (50) |
| Cost overrun (VPS + proxies) | Medium | Medium | Track K8; provider mix; auto-scale down on idle |
| Google Maps DOM/anti-bot changes | Medium | High | Pin scraper version; monitor success rate; upstream updates |
| Secrets exposure before rotation (O-1) | High (current) | High | M0 first task; rotate now |
| Legal/ToS exposure of scraping at scale | — | Medium | Operator's risk acceptance; rate limits; data handling policy |

---

## 17. Open decisions

- **D1. ✅ Decided — managed Postgres.** Fleet region, ~4 vCPU/16 GB to start, backups + PITR (§12.1). A push-to-VPS path for config/keywords is provided via the control VPS (FR-6.4).
- **D2. ⏳ In progress — proxies.** Recommendation in `PROXY_OPTIONS.md`: start **per-IP/unmetered (Webshare datacenter/ISP)** to keep cost ~fixed for browser scraping, with **Evomi residential $0.49/GB** as the block-rate fallback; skip Bright Data/Oxylabs (premium). Final pick = box1 A/B (empty-rate + measured GB/day). Gates fleet sizing (O-2).
- **D3. ✅ Decided — box1 = the 18-core/64 GB box** runs the first ~12-container worker before any fleet expansion. Calibrate yield here (O-3) before adding boxes.
- **D4. ✅ Answered — one small always-on control VPS** (not the managed DB, which is a service) hosts the enqueuer/serve/exporter/global-combine/monitor and is the keyword-push target (§12.1, FR-6.4).
- **D5. ✅ Decided — `place_id` → `cid` → normalized hash** for `businesses`; phone is the dedup key for the `leads` layer (§12.4–12.5).
- **D6. ✅ Decided — `businesses`/`leads` kept indefinitely**, raw NDJSON archives kept for a short audit window (30–90 d), and `scrape_results` kept lightweight for counts/alerts (§12.7).
- **D7. ✅ Decided — continuous top-up enqueue** (see explanation below).

**D7 — why continuous top-up over once-daily batch.** *Once-daily batch* expands every active state × keyword × cell and inserts all jobs at one time. It's simple and mirrors today's cycle, but it spikes the queue to millions of rows (DB/backpressure stress), leaves workers idle once the batch drains before the next day, and makes a mid-day state change wait until tomorrow. *Continuous top-up* keeps the queue at a target depth (e.g. 2–6 hours of fleet capacity); a scheduler refills it as it drains. Workers never idle, the queue never floods, DB load is smooth, and adding/removing a state takes effect on the **next** top-up (minutes, not a day) — which is exactly the "changing state list" goal. For a 24/7 reliability target, continuous top-up wins. Keep a once-daily "rollover" only for bookkeeping (campaign/keyword-history marks, the daily 10k lead export).

---

## 18. Appendix — key code references

- Worker entrypoint & hardcoded concurrency: `util/scraper_src/cmd/gmapssaas/cmdworker/cmd_worker.go`
- Queue config, timeouts, rescue, worker registration: `util/scraper_src/rqueue/rqueue.go`
- Worker result writer (spool-and-ship: per-job → NDJSON + `.done`): `auto_scrape/util/scraper_src/scraper/centralwriter.go` (`pgSave`, Part C). Loader: `scale/worker/shipper.py`. Apply-ready change: `scale/scraper_changes/centralwriter_businesses_change.md`.
- Enqueue API: `util/scraper_src/api/api.go` (`POST /api/v1/scrape` → `rqueue.Client.InsertJob`)
- Provisioning / cloud-init / compose scaling: `util/scraper_src/infra/vps/`, `util/scraper_src/infra/cloudinit/cloudinit.go`
- Admin dashboard: `util/scraper_src/admin/`
- Dedup helper: `util/scraper_src/deduper/`
- Migrations (river, scrape_results, rate_limits, admin/2FA): `util/scraper_src/migrations/`
- Grid expansion (control plane): `orchestrate.py::generate_grid_cells`; keyword history: `util/keyword_db.py`
- Interim per-keyword analysis organizer (handles keyword loss in clean_csv): `scale/util/organize.py`
- Live progress monitor (FR-7.5): `scale/util/monitor.py`  (extends the SSH/row-count logic in `auto_scrape/util/stats.py`)
- Control-plane tooling: `scale/control/` (`enqueue.py`, `push_keywords.py`, `export_leads.py`, `alert.py`, `bootstrap.sh`, `active_states.yaml`, `grid.py`)
- Worker box files: `scale/worker/` (`docker-compose.yml`, `.env.example`, authentic `Dockerfile.saas`)
- Proposed scraper-repo edits + upstream files: `scale/scraper_changes/` — `REPO_SETUP.md` (own the fork), `businesses`/`leads` migration, the `centralwriter.go` patch, authentic `docker-compose.saas.yaml`
- Upstream source confirmed open-source: `github.com/gosom/google-maps-scraper` (SaaS edition on `main`)
- Global-combine lead pipeline reference (manifest dedup-append + 10k chunker + area-code→state): the operator's `main.py` (FR-4.6, §12.5)

---

### Glossary
- **Cell** — one grid square of a state's bbox; the unit of geographic search.
- **Job** — one `(keyword × cell)` scrape task in the River queue.
- **Worker container** — one Docker replica; processes one job at a time.
- **Net-unique record** — a deduplicated business row in the `businesses` table.
- **Net-unique/container/day** — the calibration number that drives fleet size.
