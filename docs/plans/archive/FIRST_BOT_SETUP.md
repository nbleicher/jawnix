# First Bot Setup — box1 (18 vCPU / 64 GB)

| | |
|---|---|
| **Goal** | Get the **first worker box** running the queue/worker model correctly, prove it end-to-end, and **measure per-container yield (O-3)** before buying any fleet. |
| **Box** | box1 = the 18-core / 64 GB VPS (decision D3). Target: **~12 worker containers**. |
| **This is** | Milestone **M1**. Done right, it produces the single number that sizes everything else. |
| **Status** | Plan — files in `worker/`; one blocker to clear (§2, the missing `Dockerfile.saas`, now drafted). |

> **The one rule that makes or breaks this:** in worker mode, **one container = one browser = one job at a time** (the code hardcodes it). You get throughput by running **many containers**, never by a `-c` flag and never by running the bare binary. Running a single worker is exactly what produced "a couple dozen rows overnight" last time.

---

## 1. Prerequisites (must be true before box1 is useful)

box1 is a *consumer* of a queue. These have to exist first:

1. **Managed Postgres** is up and reachable from box1 over the private network (D1).
2. **Migrations applied** — incl. `businesses`/`leads` (from `scale/scraper_changes/migrations/`), and the worker binary **built with the spool patch** (`scale/scraper_changes/centralwriter_businesses_change.md`, Part B + C). The worker writes NDJSON; `shipper.py` loads `businesses`. See `SETUP_POSTGRES_CONTROL_VPS.md` + `SPOOL_AND_SHIP.md`.
3. **Control VPS** is running `gmapssaas serve` (the queue API) and the **enqueuer** can insert jobs (`control/enqueue.py`). See `CONTROL_VPS_PLAN.md`.
4. **Secrets:** box1's `.env` has `DATABASE_URL` (same DB) and `HASHID_SALT` **identical to the control VPS** (job IDs are hashed with it).
5. **Proxy decision pending (D2):** start raw-IP for the baseline; have one proxy endpoint ready for the A/B in §6.

> If Postgres + control VPS aren't up yet, do that first. box1 alone scrapes nothing — it pulls from the queue.

---

## 2. Clear the blocker: the worker image

The Makefile's `docker-saas` target references `Dockerfile.saas`, which was **missing from your `scraper_src` copy** — so there was no image to build. The fix is simple: that file exists in the **public gosom repo** (the SaaS edition is open-source on `main`), so we use the maintainer's real one. `scale/worker/Dockerfile.saas` is now that authentic file. (To also restore `docker-compose.saas.yaml` and put `scraper_src` under your own version control, follow `scale/scraper_changes/REPO_SETUP.md` first.) Action:
1. Copy `scale/worker/Dockerfile.saas` into `auto_scrape/util/scraper_src/` (next to `go.mod`).
2. Build it: `docker build -f Dockerfile.saas -t gmapssaas:local auto_scrape/util/scraper_src` (or let compose build it, §3).
3. **Verify the browser launches** in §4's smoke test before scaling.

Notes on this image: it's a two-stage build (Go → `debian:bookworm-slim` with Chromium's system libs). **Chromium itself is downloaded at runtime by `playwright-go`**, so there's no Playwright version to pin — but each container needs outbound network on first start, and that first launch is slower. `CMD` defaults to `serve`; the worker compose overrides it with `command: ["worker"]`.

---

## 3. Provision box1 (one script)

Assemble the repo on the box (auto_scrape + `scale/worker/`, with Go-patch **Part B + C** applied), then run the worker bootstrap. It installs Docker + deps, writes `worker/.env`, **builds the image**, and installs the event-driven shipper units — and stops at "ready" (it does **not** bring up or scale; those stay manual gates below).

```bash
# OS: Ubuntu 22.04+. As root. Repo assembled at /opt/gms-worker.
sudo APP_DIR=/opt/gms-worker SPOOL_DIR=/data/incoming \
     DATABASE_URL='postgres://USER:PASS@PG-HOST:5432/gmaps_pro?sslmode=require' \
     HASHID_SALT='<same as control VPS>' PROXIES='' \
     bash /opt/gms-worker/worker/bootstrap.sh
```

It prints the exact smoke-test → scale commands when done. `worker/docker-compose.yml` runs 1.5 cpu / 2 g / `shm 1g` per container (Chromium hangs without real `/dev/shm`) and mounts the `/data/incoming` spool. **Spool-and-ship** (`SPOOL_AND_SHIP.md`): the worker writes NDJSON; the shipper (installed by bootstrap, event-driven on `.done` markers) fills `businesses` — so the smoke test's "`businesses` rising" happens via the shipper.

> **Fleet:** after box1 works, push `gmapssaas:local` to a registry and run this same script on each new box with `IMAGE=<registry/image>` to **pull** instead of rebuilding.

---

## 4. Smoke test with ONE container (do not skip)

Prove the browser + queue path end-to-end before scaling. This catches the two failure modes that killed the last attempt (browser won't launch; queue not actually consumed).

```bash
cd /opt/gms/worker
docker compose build                 # builds gmapssaas:local from Dockerfile.saas
docker compose up -d worker          # exactly ONE container
```

From the **control VPS**, enqueue a tiny batch (1 state, a few keywords, small depth):
```bash
# temporarily set active_states.yaml to a single state + queue.target_depth ~20
python3 control/enqueue.py --once
```

Watch the one container actually work:
```bash
docker compose exec worker curl -s localhost:8080/health   # expect: active_jobs=1, results_per_minute>0, jobs_processed climbing
docker compose logs -f worker                               # see jobs picked up + results saved
```

Confirm rows land in Postgres:
```sql
SELECT count(*) FROM businesses;          -- climbing
SELECT count(*) FROM businesses WHERE phone <> '';   -- phones (your lead unit)
```

**Pass criteria:** `active_jobs=1`, `results_per_minute > 0`, `businesses` count rising, no crash loop. If `results_per_minute` stays 0 → browser/image problem (§2) or you're blocked (no proxy → §6). If `active_jobs=0` while jobs are queued → queue/HASHID_SALT mismatch.

---

## 5. Scale to ~12 containers + calibrate (O-3)

```bash
docker compose up -d --scale worker_replica=11    # 1 worker + 11 replicas = 12
docker ps | grep gms | wc -l                      # expect 12
```

Keep the queue fed from the control VPS (continuous top-up so workers never idle):
```bash
python3 control/enqueue.py --watch    # set active_states.yaml to your real states + target_depth ~5000
```

Watch fleet progress from the laptop or control VPS:
```bash
python3 control/monitor.py --watch    # total phones, per-state, rate (phones/hour)
```

**Calibrate over ~12–24 h** — this is the whole point of box1:
- **Net-unique/day** = increase in `SELECT count(*) FROM businesses` over the window.
- **Per-container/day** = net-unique/day ÷ 12.  ← the number that sizes the fleet.
- **Block/empty rate (K5)** = share of jobs returning 0 results.
- **Utilization (K3)** = containers with `active_jobs=1` ÷ 12 (want ≥80%).

---

## 6. Proxy A/B (D2 / O-2)

**Step 0 — measure bandwidth with Webshare's free 10 proxies (do this first).**
The free tier is datacenter/shared and bandwidth-capped — perfect for sampling GB-per-job, not for volume. In `worker/.env` set `PROXIES=` to the 10 endpoints (comma-separated `http://user:pass@host:port`), run the **one-container** smoke test (§4) for a known number of completed jobs, then:
```
records = SELECT count(*) FROM businesses;          # over the test window
GB_used = Webshare dashboard → bandwidth used
# → GB per 1,000 records = GB_used / records * 1000
# → projected residential cost/day = (GB per 1k) × (records/day ÷ 1000) × $/GB
```
This single number decides **per-GB vs per-IP**: if browser bandwidth is high, go per-IP/unmetered (Webshare DC/ISP); if low (or with `fast_mode`), Evomi $0.49/GB residential is viable. The free cap will run out fast — *how* fast is itself the signal.

**Then the real A/B (before committing spend):**
- **Arm A (raw):** containers with `PROXIES=` empty.
- **Arm B (proxied):** containers with `PROXIES=` set — second compose project (`-p box1b`) with its own `.env`.
- Compare net-unique/day and empty-rate. If raw's empty-rate spikes after the first hour, that's the soft-block — proxies are mandatory and the planning yield comes from Arm B.

---

## 7. Validation checklist

- [ ] `Dockerfile.saas` builds; image runs (§2).
- [ ] One container passes the smoke test: `results_per_minute > 0`, rows in `businesses` (§4).
- [ ] 12 containers up; `active_jobs` ≈ 12; utilization ≥ 80%.
- [ ] Continuous top-up keeps queue depth in band (never 0, never flooding).
- [ ] Killing one container → its job re-runs within ≤20 min, no duplicate row (River rescue).
- [ ] Per-container/day, block-rate, and the proxy A/B result are recorded.

## 8. Common failure modes → cause

| Symptom | Cause | Fix |
|---|---|---|
| "A couple dozen rows overnight" | One worker / bare binary (concurrency=1 trap) | Run 12 **containers** via compose (§5) |
| `results_per_minute = 0`, jobs time out | Browser won't launch (Playwright/shm) | §2 image; ensure `shm_size: 1g` |
| Near-zero rows after ~1 h, jobs "succeed" empty | IP soft-block | Add proxies (§6) |
| `active_jobs = 0` while queued | Wrong DB / HASHID_SALT mismatch / serve down | Match `.env` to control VPS; check `serve` |
| Jobs stuck "running" then retried | Container died mid-job | Normal — River rescues after ≤20 min |

## 9. Exit criteria (M1 done)

box1 sustains a **measured, repeatable net-unique/day**, ≥80% utilization, deduped rows in `businesses`, and you hold the **per-container/day** figure (with vs. without proxies). Plug that into PRD §10 to decide how many more boxes hit 250k/day — *then* provision the fleet (which reuses this exact image + compose via cloud-init).

---

### Files for this step
- `worker/docker-compose.yml` — box1 compose (12 containers)
- `worker/.env.example` — env template (DATABASE_URL, HASHID_SALT, PROXIES…)
- `worker/Dockerfile.saas` — draft worker image (verify, §2)
- `control/enqueue.py` — feeds the queue · `control/monitor.py` — watches progress
