# Control VPS — Plan & Utility Inventory

| | |
|---|---|
| **Purpose** | The always-on brain of the scraping platform: enqueues work, owns the queue API, exports leads, and is the push target for keyword updates. |
| **Decisions** | D1 managed Postgres · D4 control plane on a small VPS · D7 continuous top-up |
| **Status** | Plan — utilities built (`scale/control/`); box not yet provisioned |
| **Last updated** | 2026-06-17 |

> **Layout note:** the control-plane tooling lives in `scale/control/`; the scraper app + Go binary come from `auto_scrape/`; proposed code edits are in `scale/scraper_changes/`. The control VPS runs an **assembled** app dir = `auto_scrape/` + `scale/control/` with `scraper_changes/` applied (see `SETUP_POSTGRES_CONTROL_VPS.md` Part B2).

---

## 1. Role & boundaries

The control VPS is **one small, cheap, always-on box** (1–2 vCPU / 2–4 GB). It runs only light, I/O-bound coordination work.

**It IS:** the queue API (`serve`), the continuous top-up enqueuer, the daily global-combine lead export, the admin/control panel, the live monitor, and the destination for pushed `keywords.txt`.

**It is NOT:** a browser worker (those are box1 + the future fleet) and **not** the database (that's managed Postgres). Keeping it separate means a worker crash, a DB failover, or a laptop being offline never takes the control plane down.

```
   Laptop ──push_keywords.py──▶ Control VPS ──InsertJob──▶ Managed Postgres ◀──pull jobs── Worker fleet
   (you)                        (this plan)     (River queue + businesses/leads)            (box1 = 18c/64GB)
                                    │                                  ▲
                                    └── enqueue.py (top-up) ───────────┘
                                    └── export_leads.py (daily 10k CSVs ← leads)
                                    └── gmapssaas serve/admin · monitor.py
```

---

## 2. Spec

- **Size:** 1–2 vCPU, 2–4 GB RAM, 25–50 GB disk. (Control work is light; exports stream to disk then off-box.)
- **OS:** Ubuntu 22.04+.
- **Region:** same region/provider as the managed Postgres for low-latency inserts.
- **Network:** SSH (22) open to you only; the queue API (:8080) and admin panel (:8090) reachable over the private network or an SSH tunnel — **not** the public internet. Postgres reached over the provider's private network + TLS.

---

## 3. Services running on the box

| Service (systemd) | Command | Port | Role |
|---|---|---|---|
| `gms-serve` | `gmapssaas serve --addr :8080` | 8080 | Queue API (`POST /api/v1/scrape`) + River maintenance jobs |
| `gms-admin` | `gmapssaas admin --addr :8090` | 8090 | Web **control panel** — jobs/workers, auth + 2FA |
| `gms-enqueue` | `python control/enqueue.py --watch` | — | Continuous top-up producer (D7) |
| `gms-export.timer` | `python control/export_leads.py --daily` | — | Daily global-combine lead append + 10k CSV export |
| `gms-alert.timer` | `python control/alert.py …` | — | Every 10 min: alert on queue/pace/empty-rate/reachability (O-10) |

All read secrets from `/home/scraper/scraper/.env` (chmod 600). `restart=always` keeps the long-running ones alive; the export is a daily `oneshot` timer (08:00 UTC).

---

## 4. Directory layout (assembled on the box)

```
/home/scraper/scraper/          # APP_DIR = auto_scrape/ + scale/control/ + applied scraper_changes
  gmapssaas              # built Go binary (serve/admin/worker), incl. the spool patch (Part B+C)
  .env                   # DATABASE_URL, HASHID_SALT, ENCRYPTION_KEY  (chmod 600, never committed)
  keywords.txt           # active keyword list — push target
  control/               # ← from scale/control/
    active_states.yaml   # the changing state list (edit to add/drop states)
    enqueue.py           # continuous top-up producer
    grid.py              # vendored state bboxes + cell generator
    export_leads.py      # global-combine: businesses → leads + daily 10k CSVs
    push_keywords.py     # (also runs from your laptop)
    bootstrap.sh         # this box's provisioning script
    monitor.py           # (copy of scale/util/monitor.py, or run from laptop)
  exports/               # lead_<date>_<n>.csv daily outputs
  util/, orchestrate.py, ...   # rest of auto_scrape (migrations live under util/scraper_src/migrations)
```

---

## 5. Secrets & access

- **No plaintext secrets in git** (closes O-1 for this box): `DATABASE_URL`, `HASHID_SALT`, `ENCRYPTION_KEY` live only in `.env` (chmod 600), generated at provision time.
- **Key-based SSH only**; the laptop's key is the only authorized key. `push_keywords.py` uses that key (or an agent).
- Provider/proxy credentials encrypted at rest via `app_config` + `ENCRYPTION_KEY` (`cryptoext`).

---

## 6. Utility inventory

Everything the control plane needs. **Built** = delivered in `scale/`; **Exists** = already in `auto_scrape/`; **Build later** = needs the live queue/DB or remains a worker-side concern.

| Utility | Runs where | Status | What it does |
|---|---|---|---|
| `scale/control/push_keywords.py` | **Laptop** | ✅ Built | Validate + diff a local `keywords.txt`, atomically upload to the control VPS, optionally fire a trigger. Also `--local` mode for the current Mac-orchestrator. |
| `scale/control/enqueue.py` | Control VPS | ✅ Built | Continuous top-up producer (D7): active states × keywords × cells → `POST /api/v1/scrape`, capped at `target_depth`. Grid logic vendored in `grid.py`; `--dry-run` works offline. |
| `scale/control/active_states.yaml` | Control VPS | ✅ Built | The single config you edit to change coverage (add/drop a 2-letter state). |
| `scale/control/export_leads.py` | Control VPS | ✅ Built | Global-combine: append phone-deduped leads from `businesses` into `leads`, export today's new leads as 10k-row CSVs. |
| `scale/control/alert.py` | Control VPS | ✅ Built | Push alerts (O-10) on queue depth / pace / empty-rate / API+DB reachability; webhook or exit-code. |
| `scale/scraper_changes/migrations/…control-ledger.sql` | applied to DB | ✅ Built | `enqueue_log` (O-11 idempotency) + `keyword_history` (O-12 campaign dedup). |
| `scale/control/bootstrap.sh` | Control VPS | ✅ Built | One-shot provisioning: deps, venv, build binary, `.env`, migrations, systemd units, firewall. |
| `scale/util/monitor.py` | Either | ✅ Built | Live total phones scraped + per-state/instance + rate (FR-7.5). |
| `auto_scrape/util/stats.py` | Either | Exists | One-shot job/row stats per instance. |
| `gmapssaas serve` | Control VPS | Exists (Go) | Queue API + River maintenance. |
| `gmapssaas admin` | Control VPS | Exists (Go) | Web control panel (auth + 2FA). |
| `sql-migrate` | Control VPS | Exists | Runs the `rubenv/sql-migrate` migrations (incl. the new `businesses`/`leads`). No `gmapssaas migrate` subcommand exists. |
| `scale/scraper_changes/migrations/…businesses_and_leads.sql` | applied to DB | ✅ Built | New schema; copy into `auto_scrape/util/scraper_src/migrations/` before migrating. |
| `scale/scraper_changes/centralwriter_businesses_change.md` | applied to repo | ✅ Built | The O-5 + FR-4.4 Go change (worker writes `businesses` directly, with `state`/`cell`). Apply before building the binary. |
| `scale/scraper_changes/REPO_SETUP.md` | one-time | ✅ Built | Own `scraper_src` as your git repo + restore the 2 upstream files + apply the patch (instead of re-forking). |
| `scale/worker/Dockerfile.saas` · `scale/scraper_changes/docker-compose.saas.yaml` | applied to repo | ✅ Built | **Authentic** upstream files (gosom `main`) that were missing from your copy. Dockerfile = worker image; compose = local-dev Postgres only. |
| `auto_scrape/orchestrate.py` | Control VPS (interim) | Exists | Current `-web` daily cycle; can run here once the box replaces the laptop, until the queue model is live. |
| `main.py` (operator's) | Reference | Exists | The lead-distribution pipeline whose global-combine logic `export_leads.py` ports to the DB. |
| Worker fleet (`gmapssaas worker`) | Worker boxes | Build later | Not on the control VPS — runs on box1 (18c/64GB) + fleet (see `FIRST_BOT_SETUP.md`). |

---

## 7. Provisioning (one command)

1. Create the VPS; add your SSH key.
2. Assemble the app dir (auto_scrape + scale/control + scraper_changes) — see `SETUP_POSTGRES_CONTROL_VPS.md` Part B2.
3. Edit the CONFIG block at the top of `control/bootstrap.sh` (`DATABASE_URL` → managed Postgres).
4. `sudo bash control/bootstrap.sh` — installs deps, builds the binary, runs migrations, writes `.env`, installs + enables the systemd units, sets the firewall.
5. Verify: `systemctl status gms-serve gms-admin gms-enqueue` and open the admin panel on :8090 over a tunnel.

---

## 8. Operational flows

**Change keywords (from your laptop):**
```
python3 control/push_keywords.py keywords.txt --host <control-vps> --trigger-run
```
Validates/dedupes, shows the added/removed diff, uploads atomically, and (with `--trigger-run`) kicks the enqueuer to pick up the new list immediately.

**Change coverage (states):** edit `control/active_states.yaml` on the box (add/remove a 2-letter code). The next top-up reflects it — no redeploy (the G2 goal).

**Continuous top-up (always running):** `gms-enqueue` keeps the queue near `target_depth`; workers never idle, the queue never floods.

**Daily leads (08:00 UTC):** `gms-export.timer` appends new phone-deduped leads to the global sheet and drops 10k-row CSVs into `exports/`.

**Watch progress:** `python3 control/monitor.py --watch` (or the admin panel).

---

## 9. Current → future bridge

You can stand this box up **now** and get value before the full queue model is live:
- Move `orchestrate.py` + `keywords.txt` here so the daily `-web` cycle no longer depends on your laptop (closes O-7 early). `push_keywords.py` already targets it; `monitor.py` already reads the current fleet.
- When Postgres + the worker fleet are up, flip the producer from `orchestrate.py` (SSH submit) to `enqueue.py` (queue insert) and start `export_leads.py`. Same box, same keyword-push flow.

---

## 10. Open items before this goes live

- **D2 proxies** — still your open decision; gates the worker fleet, not the control VPS itself.
- **Own + complete `scraper_src`** per `scale/scraper_changes/REPO_SETUP.md`: restore the two authentic upstream files (`Dockerfile.saas`, `docker-compose.saas.yaml`), apply the `businesses`/`leads` migration and the `centralwriter.go` patch — all before the binary is built.
- **Confirm `gmapssaas` subcommand names** (`serve` / `admin`) match the binary (`./gmapssaas --help`); adjust the systemd units in `bootstrap.sh` if they differ.
