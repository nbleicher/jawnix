# Setup — Managed Postgres + Control VPS

Step-by-step bring-up of the two pieces that must exist before the first worker (box1) does anything. End state: a managed Postgres with the full schema, and a small always-on control VPS running the queue API, admin panel, and the continuous-top-up enqueuer — reachable from your laptop via `push_keywords.py`.

**Order:** Postgres first (the control VPS and workers both point at it), then the control VPS.

**You'll need:** the repo, an SSH key, and a provider account (Hetzner / DigitalOcean / Neon, etc.).

---

## Part A — Managed Postgres

### A1. Provision
Create a **managed PostgreSQL 15+** instance in the region you'll run workers in.
- Start size: **~4 vCPU / 16 GB / 100 GB SSD** (scales vertically later).
- Enable **automated backups + PITR** and a standby if offered.
- Database name: **`gmaps_pro`** (matches `migrations/dbconfig.yml`).

### A2. Network & TLS
- Restrict inbound to the **private network / allowlist** containing the control VPS and worker boxes. Avoid exposing 5432 publicly.
- Require TLS (`sslmode=require`).

### A3. Get the DSN
```
postgres://USER:PASSWORD@PG-HOST:5432/gmaps_pro?sslmode=require
```
Keep it only in `.env` files (chmod 600). Never commit it.

### A4. Run migrations
Migrations use the **`sql-migrate` CLI** (there is no `gmapssaas migrate` subcommand). From a machine that can reach the DB and has Go + the repo (the control VPS works, or do it once from your laptop):

```bash
cd util/scraper_src

# install the migration tool
GOBIN="$PWD/bin" go install github.com/rubenv/sql-migrate/...@latest

# point a prod dbconfig at the managed DB
cat > migrations/dbconfig.prod.yml <<'YAML'
production:
  dialect: postgres
  datasource: ${DATABASE_URL}
  dir: migrations
  table: migrations
YAML

# run all pending migrations (River tables, scrape_results, admin/2FA,
# rate_limits, AND the new businesses + leads tables)
export DATABASE_URL='postgres://USER:PASSWORD@PG-HOST:5432/gmaps_pro?sslmode=require'
./bin/sql-migrate up -config=migrations/dbconfig.prod.yml -env=production
./bin/sql-migrate status -config=migrations/dbconfig.prod.yml -env=production
```

> The `businesses`/`leads` schema lives in **`scale/scraper_changes/migrations/20260617000000-add_businesses_and_leads.sql`** (kept out of the clean scraper repo). Copy it into the repo's `migrations/` **before** running the command above:
> ```bash
> cp /path/to/scale/scraper_changes/migrations/20260617000000-add_businesses_and_leads.sql \
>    auto_scrape/util/scraper_src/migrations/
> ```
> DDL reference: PRD §12.

### A5. Verify
```bash
psql "$DATABASE_URL" -c "\dt"          # expect river_job, scrape_results, businesses, leads, rate_limits, app_config, admin tables...
psql "$DATABASE_URL" -c "\d businesses"
```

---

## Part B — Control VPS

### B1. Provision
- Small box: **1–2 vCPU / 2–4 GB**, Ubuntu 22.04+, same region as the DB.
- Add your SSH **public key**; disable password login.
- Firewall: allow 22 only; the API (:8080) and admin (:8090) stay on the private net / SSH tunnel.

### B2. Assemble the app dir on the box
The control VPS needs the **scraper repo** (to build the `gmapssaas` binary + hold the migrations) **plus** the `scale/control/` scripts, with `scraper_changes/` applied. First make `scraper_src` a complete, owned repo per **`scale/scraper_changes/REPO_SETUP.md`** (restores the two authentic upstream files — `Dockerfile.saas`, `docker-compose.saas.yaml` — and applies the patch). Then assemble into one `APP_DIR` (`/home/scraper/scraper`):
```bash
APP=root@CONTROL-VPS:/home/scraper/scraper
# 1) the scraper app (already completed per REPO_SETUP.md: 2 upstream files + Go change applied)
rsync -az auto_scrape/  $APP/
# 2) the control-plane tooling
rsync -az scale/control/ $APP/control/
# 3) the new migration (if not already committed into the repo)
rsync -az scale/scraper_changes/migrations/ $APP/util/scraper_src/migrations/
```

### B3. Configure + bootstrap
`control/bootstrap.sh` does the heavy lifting (deps, Go, build the `gmapssaas` binary, `.env`, migrations, systemd units, firewall). Edit its CONFIG block first:

```bash
# on the control VPS
cd /home/scraper/scraper/control
sudo DATABASE_URL='postgres://USER:PASSWORD@PG-HOST:5432/gmaps_pro?sslmode=require' \
     bash bootstrap.sh
```
It generates `HASHID_SALT` and `ENCRYPTION_KEY` into `/home/scraper/scraper/.env` (chmod 600). **Record the `HASHID_SALT`** — every worker's `.env` must use the *same* value (job IDs are hashed with it).

### B4. Create the admin user (for the control panel)
```bash
cd /home/scraper/scraper
set -a; . .env; set +a
./gmapssaas admin create-user -u admin -p 'CHOOSE-A-STRONG-PASSWORD'
```

### B5. Confirm services
```bash
systemctl status gms-serve gms-admin gms-enqueue gms-export.timer
curl -s localhost:8080/api/v1/jobs | head        # serve API responds
# admin panel on :8090 — reach over an SSH tunnel:
#   ssh -L 8090:localhost:8090 root@CONTROL-VPS   then open http://localhost:8090
```

The systemd units are:
- `gms-serve` — queue API (`POST /api/v1/scrape`) + River maintenance
- `gms-admin` — web control panel (auth + 2FA)
- `gms-enqueue` — continuous top-up producer (`control/enqueue.py --watch`)
- `gms-export.timer` — daily global-combine lead export (08:00 UTC)

---

## Part C — Connect & verify end-to-end

### C1. Point your laptop at the control VPS
Add a `control:` block to `config.yaml` (used by `push_keywords.py`):
```yaml
control:
  host: CONTROL-VPS
  user: root
  key: ~/.ssh/your_key
  remote_keywords: /home/scraper/scraper/keywords.txt
  trigger: "cd /home/scraper/scraper && ./venv/bin/python control/enqueue.py --once"
```

### C2. Push keywords + set coverage
```bash
# from laptop
python3 control/push_keywords.py keywords.txt           # validate + diff + upload
# on the control VPS: edit which states are active
nano /home/scraper/scraper/control/active_states.yaml    # add/remove 2-letter codes
```

### C3. Enqueue a tiny test + watch
```bash
# control VPS — small batch to confirm the path before real load
./venv/bin/python control/enqueue.py --dry-run           # shows job counts, inserts nothing
./venv/bin/python control/enqueue.py --once              # inserts one batch
psql "$DATABASE_URL" -c "SELECT count(*) FROM river_job;"  # jobs queued
python3 control/monitor.py --watch                       # once workers run, totals climb
```

When box1 (the worker) is up (`FIRST_BOT_SETUP.md`), `gms-enqueue` keeps the queue topped up automatically.

---

## Part D — Populating `businesses` / `leads`

**Spool-and-ship (chosen):** the worker (built with Part B + C) writes results as NDJSON + `.done` markers to a local spool on the worker box; `worker/shipper.py` (event-driven, installed by `worker/bootstrap.sh`) bulk-loads them into `businesses`. This runs on **box1**, not the control VPS — see `FIRST_BOT_SETUP.md` §3 + `SPOOL_AND_SHIP.md`. So `businesses` is populated by the shipper, not by direct worker writes.

Then `control/export_leads.py --daily` (on the control VPS) rolls `businesses` → `leads` (phone-deduped, area-code state) and writes the 10k-row CSVs. Once box1 is scraping and the shipper is running, `businesses` fills and the daily lead export just works.

---

## Validation checklist
- [ ] `\dt` shows `river_job`, `scrape_results`, `businesses`, `leads`, `rate_limits`, `app_config`, admin tables.
- [ ] `sql-migrate status` shows all migrations applied.
- [ ] `gms-serve` answers on :8080; admin panel loads on :8090 via tunnel; admin user created.
- [ ] `enqueue.py --once` inserts jobs (`river_job` count rises).
- [ ] `HASHID_SALT` recorded and reused for workers.
- [ ] DSN only in chmod-600 `.env` files; nothing secret committed.

## Security notes
- No plaintext secrets in git (this is the O-1 closure for these boxes): rotate the old shared password, use key-based SSH, keep `.env` at chmod 600.
- DB reachable only from the private network + TLS; API/admin not public.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| `sql-migrate` can't connect | DSN/network/TLS | Check allowlist + `sslmode=require`; test with `psql "$DATABASE_URL" -c '\l'` |
| `enqueue.py` "queue API unreachable" | `gms-serve` down or wrong `api_base` | `systemctl status gms-serve`; check `active_states.yaml: api_base` |
| Workers connect but `active_jobs=0` | `HASHID_SALT` mismatch between serve and workers | Make all `.env` salts identical |
| `gmapssaas` subcommands differ | binary built from a different rev | `./gmapssaas --help` and adjust systemd units in `bootstrap.sh` |

---

### Files used here
- `control/bootstrap.sh` — control VPS provisioning
- `scale/scraper_changes/migrations/20260617000000-add_businesses_and_leads.sql` — new schema (copy into `auto_scrape/util/scraper_src/migrations/`)
- `scale/scraper_changes/centralwriter_businesses_change.md` — the worker Go change (apply before building)
- `control/enqueue.py`, `control/active_states.yaml`, `control/export_leads.py`, `control/push_keywords.py`, `control/monitor.py`
- Next: `FIRST_BOT_SETUP.md` (the worker box)
