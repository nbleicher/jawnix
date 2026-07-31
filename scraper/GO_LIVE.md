# Go-Live Checklist — first bot (box1)

One sheet to take the first bot live. Steps are ordered; each notes which PRD open-issue(s) it closes. **Proxies (O-2) are intentionally out of scope here** — they're your separate decision and only gate scaling past box1, not bring-up.

Legend: ☐ to do · 🔧 runs on a real machine (can't be done from chat).

---

## Phase 0 — Code & secrets (local, no infra needed)

**0.1 ☐ Own + complete the scraper repo** — follow `scraper_changes/REPO_SETUP.md`:
- restore the two authentic upstream files (`Dockerfile.saas`, `docker-compose.saas.yaml`),
- copy in the migration, edit `scraper/centralwriter.go` per `scraper_changes/centralwriter_businesses_change.md` (Part A + B),
- `git init` → private GitHub repo. *(Closes O-4, O-5; sets up O-6/O-7 plumbing.)*

**0.2 🔧 ☐ Rotate + purge secrets (O-1).** Independent of proxies — do it now.
> A `.gitignore` does **not** fix this: the password (`Trixie12`) and `test_ssh_key` are already in git *history*. The real fix is **rotation** (it makes the leaked value worthless); `.gitignore`/`.env` only prevent *future* leaks; history purge is hygiene. Do them in this order:

```bash
# (a) ROTATE — the actual fix. Change the VPS root passwords and, better, switch
#     SSH to key-only (then there is no password to protect). Revoke the old key.
#     Once rotated, the value in history is dead regardless of steps b/c.

# (b) STOP committing secrets going forward:
#     - move VPS passwords OUT of config.yaml into a git-ignored .env (code reads env vars)
#     - add .gitignore:
cd auto_scrape
cat >> .gitignore <<'EOF'
.env
*_ssh_key
*.pem
EOF
git rm --cached util/scraper_src/local/test_ssh_key   # stop tracking (still in history until c)

# (c) PURGE history (do if the repo is shared/public; optional-but-recommended if private):
pip install git-filter-repo
git filter-repo --path util/scraper_src/local/test_ssh_key --invert-paths
printf 'Trixie12==>REDACTED\n' > /tmp/redact.txt
git filter-repo --replace-text /tmp/redact.txt
git push --force --all && git push --force --tags     # coordinate if anyone else has clones
```
*(Closes O-1. Rotation alone neutralizes the exposure; b + c are cleanliness.)*

**0.3 🔧 ☐ Build + verify the Go change.** It was never compiled — do this before trusting it.
```bash
cd auto_scrape/util/scraper_src
go build ./...                          # must compile (incl. patched centralwriter.go)
go vet ./scraper/ ./rqueue/ ./api/
go test ./scraper/ ./rqueue/            # signature change touches these tests — update call sites if they fail
```
*(Verifies O-4/FR-4.4 code.)*

---

## Phase 1 — Managed Postgres

**1.1 🔧 ☐ Provision** managed Postgres (D1), DB `gmaps_pro`, private network + TLS, backups/PITR. *(Closes O-8.)*

**1.2 🔧 ☐ Migrate** (per `SETUP_POSTGRES_CONTROL_VPS.md` A4):
```bash
cd auto_scrape/util/scraper_src
GOBIN="$PWD/bin" go install github.com/rubenv/sql-migrate/...@latest
cat > migrations/dbconfig.prod.yml <<'YAML'
production: {dialect: postgres, datasource: ${DATABASE_URL}, dir: migrations, table: migrations}
YAML
export DATABASE_URL='postgres://USER:PASS@PG-HOST:5432/gmaps_pro?sslmode=require'
./bin/sql-migrate up     -config=migrations/dbconfig.prod.yml -env=production
./bin/sql-migrate status -config=migrations/dbconfig.prod.yml -env=production
```

**1.3 🔧 ☐ Verify** `\dt` shows `river_job, scrape_results, businesses, leads, rate_limits, app_config` + admin tables.

---

## Phase 2 — Control VPS

**2.1 🔧 ☐ Provision** a small VPS (1–2 vCPU); SSH key only; firewall 22 only.
**2.2 🔧 ☐ Assemble + bootstrap** (per `SETUP_POSTGRES_CONTROL_VPS.md` B2–B3): rsync `auto_scrape/` + `scale/control/`, then `sudo DATABASE_URL=... bash control/bootstrap.sh`. **Record `HASHID_SALT`.** *(Closes O-7.)*
**2.3 🔧 ☐ Admin user:** `./gmapssaas admin create-user -u admin -p '...'`.
**2.4 🔧 ☐ Confirm services:** `systemctl status gms-serve gms-admin gms-enqueue`; `curl -s localhost:8080/api/v1/jobs`.
**2.5 🔧 ☐ Set coverage + keywords:** edit `control/active_states.yaml`; from laptop `python3 control/push_keywords.py keywords.txt --host <vps>`. *(Closes O-6.)*
> Verify here: `gmapssaas` subcommand names match `bootstrap.sh` (`./gmapssaas --help`), and `enqueue.py`'s pending-count matches the real `/api/v1/jobs` response.

---

## Phase 3 — Box1 (the bot)

**3.1 🔧 ☐ Provision box1 — one script.** Repo assembled at `/opt/gms-worker` with Go-patch **Part B + C** applied, then:
```bash
sudo APP_DIR=/opt/gms-worker SPOOL_DIR=/data/incoming \
     DATABASE_URL='postgres://…/gmaps_pro?sslmode=require' HASHID_SALT='<same as control VPS>' PROXIES='' \
     bash /opt/gms-worker/worker/bootstrap.sh
```
Installs Docker + deps, writes `worker/.env`, **builds the image**, installs the event-driven shipper (`gms-ship.path`/`.service`/`.timer`). Stops at "ready" — does not run/scale. *(Mitigates O-9; decouples scrape from DB.)*
**3.2 🔧 ☐ Smoke test ONE container** (`FIRST_BOT_SETUP.md` §4): `cd /opt/gms-worker/worker && docker compose up -d worker`; enqueue a tiny batch; confirm `/health` `results_per_minute>0` and `SELECT count(*) FROM businesses` rising (via the shipper). *(Validates the whole path.)*
**3.3 🔧 ☐ Scale to 12** `docker compose up -d --scale worker_replica=11`; start `enqueue.py --watch`; watch `monitor.py --watch`.
**3.4 🔧 ☐ Calibrate** net-unique/day ÷ 12 = per-container yield. *(Closes O-3 — the number that sizes the fleet.)*

---

## Issue status after this checklist

| Issue | After go-live |
|---|---|
| O-1 secrets in git | ✅ closed by 0.2 |
| O-3 yield unmeasured | ✅ measured by 3.4 |
| O-4 record dedup | ✅ closed by 0.1/0.3 (built) + verified at 3.2 |
| O-5 results migration | ✅ resolved (spool-and-ship; raw payloads archived as NDJSON, lightweight `scrape_results` counts retained) |
| O-6 state↔infra coupling | ✅ closed by 2.5 (`active_states.yaml`) |
| O-7 control plane SPOF | ✅ closed by 2.2 |
| O-8 Postgres hosting/SPOF | ✅ closed by 1.1 (managed + backups) |
| **O-2 proxies** | ⛔ **out of scope here** — your decision; gates scaling past box1 |
| O-9 conn pooling (PgBouncer) | ⏳ **not needed for box1** (~36 conns); required before the *fleet* |
| O-10 alerting | ✅ **built** — `alert.py` + `gms-alert.timer` (enabled by `bootstrap.sh`); set `ALERT_WEBHOOK` in `.env` |
| O-11 enqueue idempotency | ✅ **built** — `enqueue_log` ledger + `target_depth` backpressure (active once the ledger migration is applied) |
| O-12 keyword_history → Postgres | ✅ **built** — Postgres `keyword_history` + `--skip-recent-days`/`queue.skip_recent_days` |
| O-13 retention/partitioning | ⏳ decided (D6), implement later — not urgent at box1 volumes |

**Honest bottom line:** completing Phases 0–3 takes the first bot live and closes the critical/high issues (O-1, O-3, O-4, O-5, O-6, O-7, O-8). What remains besides proxies (O-2) is the ⏳ row set — all either **fleet-scale concerns (O-9), polish (O-10, O-13), or minor for a single box (O-11, O-12)** — none of which block box1. They become real work when you scale out or run unattended.
