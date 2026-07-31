# Scaling Plan — Google Maps Scraper to 250k records/day

> **Note:** this is the original high-level scaling narrative. For current specifics — decisions (D1–D7), the database plan, open issues, and the roadmap — **`PRD.md` (v5) is the living source of truth.**

**Goal:** reliably scrape ~250,000 records/day across a changing list of US states.
**Inputs:** measured ~10.7k/instance/day (50k phones / 16h on 7 instances) · re-architecting · optimize for **reliability** · proxies in progress.

---

## 1. What you have today

Two cooperating layers:

**Control plane (your Mac).** `orchestrate.py` drives everything over SSH/paramiko. Daily it kills every `screen` session, SFTP-pulls each instance's CSV, restarts the instances, expands `keywords × grid cells` into jobs, and submits them to each instance's local REST API. `clean_csv.py` combines and splits output; `keyword_db.py` (SQLite) records used keyword/state pairs; a cron "stuck-job watcher" deletes frozen jobs.

**Data plane (the VPSes).** A Go fork of `gosom/google-maps-scraper`. Today you run it in **`-web` mode**: one process per state, each on its own port, each with its **own SQLite `jobs.db`** and CSV output folder.

**The key discovery:** your repo already contains a second, far more scalable mode you aren't using — a **central queue + stateless-worker** architecture (`rqueue/`, `cmd/.../cmdworker`, `postgres/`, `infra/`): workers pull jobs from a shared Postgres/River queue and write results straight to Postgres, with auto-provisioning and an admin dashboard. This is the right foundation for 250k/day; it just isn't wired up.

---

## 2. Why the current model won't get you to 250k/day reliably

- **Per-instance SQLite + daily kill/restart.** No shared view of work. Stuck jobs are *deleted* by a cron (lost work). A crashed instance silently stops until the next cycle.
- **State is bolted to a machine.** A state = a configured instance on a specific VPS. Adding/removing a state means editing `config.yaml` and restarting — wrong for a *changing* state list.
- **Control plane is your laptop.** If the Mac is offline, nothing runs. Single point of failure.
- **Dedup is keyword-level, not record-level.** The same business from overlapping cells/keywords lands repeatedly, so net-unique is lower than gross.
- **No proxies.** From raw VPS IPs, Google rate-limits then blocks at sustained volume — the true ceiling.
- **Secrets in git.** A shared root password and an SSH key are committed in plaintext.

---

## 3. Target architecture (uses code you already have)

```
                      ┌─────────────────────────────┐
                      │   Control box (always-on)    │
                      │   - enqueues keyword×cell    │
                      │     jobs by ACTIVE state list│
                      │   - admin dashboard (built)  │
                      └───────────────┬──────────────┘
                                      │ enqueue
                                      ▼
                      ┌─────────────────────────────┐
                      │   Managed Postgres            │
                      │   - River job queue           │
                      │   - businesses + leads (dedup)│
                      └───────────────┬──────────────┘
                          pull jobs    │   deduped upserts
              ┌───────────────┬────────┼────────┬───────────────┐
              ▼               ▼        ▼        ▼               ▼
          worker box     worker box  worker …  worker box    worker box
        (gmapssaas worker, stateless, each behind a rotating proxy)
```

- **States become data, not infrastructure** — enqueue jobs for whatever states are active today. Add/drop a state → change what the enqueuer inserts. No VPS edits, no restarts.
- **Workers are identical and disposable** — one dies, River re-leases its jobs (no cron-delete hack, no lost work).
- **Results land in one Postgres** with dedup, so "250k/day" means 250k *unique* records.
- **Self-healing + visibility** — River retries/leasing for free; the built-in admin dashboard shows job/worker state.

---

## 4. Capacity model

Assumptions (calibrate on box1):

| Variable | Planning value | Note |
|---|---|---|
| Net-unique records / worker-unit / day | **7,000** | conservative, after dedup/blocks/restarts |
| Target | 250,000/day | net unique |
| Reliability buffer | **1.3×** | one outage shouldn't drop you under target |

Required ≈ 250,000 ÷ 7,000 × 1.3 ≈ **~46 worker-units**, spread across ≥2 providers/regions (Hetzner + DigitalOcean, both already in `infra/`). **Proxies are a separate, likely-larger line item** sized to request volume — measure block rate before scaling.

---

## 5. Phased rollout

- **Phase 0 — Measure & secure:** instrument true net-unique/day + block rate; add proxies to one instance and A/B vs raw; get secrets out of git (rotate, keys, purge history).
- **Phase 1 — Central queue:** stand up managed Postgres + run migrations; convert a box to `gmapssaas worker`; move enqueuing to the queue (reuse the grid logic).
- **Phase 2 — Fleet out:** auto-provision workers via cloud-init; scale to the calibrated count across two providers; active-state list as config.
- **Phase 3 — Self-running:** move the control plane off the Mac; scheduler; alerting; Postgres exporter; record-level dedup.

---

## 6. Quick wins (regardless of timeline)

Add proxies (biggest lever); get the password/SSH key out of git; move the control loop onto an always-on host; add record-level dedup so daily counts reflect unique businesses.

---

## 7. Where this went next

The specifics were carried into the PRD and the runbooks in this folder:
- **`PRD.md`** — decisions, open issues, database plan, roadmap (living doc).
- **`SETUP_POSTGRES_CONTROL_VPS.md`**, **`CONTROL_VPS_PLAN.md`**, **`FIRST_BOT_SETUP.md`** — the concrete bring-up.
- **`control/`**, **`worker/`**, **`util/`**, **`scraper_changes/`** — the tooling and the proposed code changes.
