#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# worker/bootstrap.sh — provision a WORKER box (box1 + every fleet box).
# Run as root on fresh Ubuntu 22+. Brings the box to "READY to smoke-test":
# deps, spool dir, .env, worker image, and the event-driven shipper units.
#
# It deliberately does NOT bring up containers or scale — smoke-test → scale →
# calibrate stay MANUAL gates (see FIRST_BOT_SETUP.md). Provisioning is one
# command; the calibration discipline is yours.
#
# Assumes the assembled repo (auto_scrape + scale/worker, Go patch B+C applied)
# is already at APP_DIR. Edit CONFIG, then:  sudo bash worker/bootstrap.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── CONFIG ───────────────────────────────────────────────────────────────────
APP_DIR="${APP_DIR:-/opt/gms-worker}"
SCRAPER_SRC="${SCRAPER_SRC:-$APP_DIR/util/scraper_src}"   # Go source for the local image build
SCRAPER_REPO="${SCRAPER_REPO:-git@github.com:nbleicher/scraper.git}"
SCRAPER_REVISION="${SCRAPER_REVISION:-7caca2ce8122c0ffaf47ca5737a06d05a23a90ca}"
SPOOL_DIR="${SPOOL_DIR:-/data/incoming}"
DATABASE_URL="${DATABASE_URL:-postgres://USER:PASS@127.0.0.1:5432/gmaps_pro?sslmode=disable}"
HASHID_SALT="${HASHID_SALT:-CHANGEME-same-as-control-vps}"   # MUST match the control VPS
PROXIES="${PROXIES:-}"                                       # empty = raw IP baseline; set for proxied
IMAGE="${IMAGE:-}"            # leave empty on box1 (build locally); set to a registry image on fleet boxes to PULL
# ─────────────────────────────────────────────────────────────────────────────

echo "==> system deps"
apt-get update -y
apt-get install -y docker.io docker-compose-plugin python3 python3-psycopg2 rsync curl git
systemctl enable --now docker

echo "==> spool dir ${SPOOL_DIR}"
mkdir -p "${SPOOL_DIR}/archive"

echo "==> worker .env (chmod 600)"
mkdir -p "${APP_DIR}/worker"
cat > "${APP_DIR}/worker/.env" <<ENV
DATABASE_URL=${DATABASE_URL}
HASHID_SALT=${HASHID_SALT}
PROXIES=${PROXIES}
FAST_MODE=true
MAX_JOBS_PER_CYCLE=100
DISABLE_TELEMETRY=1
GMS_SPOOL_DIR=${SPOOL_DIR}
ENV
chmod 600 "${APP_DIR}/worker/.env"

echo "==> worker image"
if [ -n "${IMAGE}" ]; then
  echo "   pulling ${IMAGE} (fleet mode)"
  docker pull "${IMAGE}"
  docker tag "${IMAGE}" gmapssaas:local
else
  echo "   building gmapssaas:local from ${SCRAPER_SRC} (box1)"
  SCRAPER_REPO="${SCRAPER_REPO}" SCRAPER_REVISION="${SCRAPER_REVISION}" \
    "${APP_DIR}/scripts/fetch-scraper-source.sh" "${SCRAPER_SRC}"
  docker build -f "${SCRAPER_SRC}/Dockerfile.saas" -t gmapssaas:local "${SCRAPER_SRC}"
fi

echo "==> shipper systemd units (event-driven on .done markers + backstop timer)"
cat > /etc/systemd/system/gms-ship.service <<UNIT
[Unit]
Description=Ship spooled scraper results to Postgres
[Service]
Type=oneshot
EnvironmentFile=${APP_DIR}/worker/.env
Environment=GMS_SPOOL_DIR=${SPOOL_DIR}
ExecStart=/usr/bin/python3 ${APP_DIR}/worker/shipper.py --drain
UNIT
cat > /etc/systemd/system/gms-ship.path <<UNIT
[Unit]
Description=Trigger the shipper when a .done marker appears
[Path]
PathExistsGlob=${SPOOL_DIR}/*.ndjson.done
Unit=gms-ship.service
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/gms-ship.timer <<UNIT
[Unit]
Description=Backstop drain of the spool folder
[Timer]
OnBootSec=2min
OnUnitActiveSec=3min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now gms-ship.path gms-ship.timer

echo "==> worker heartbeat timer"
cat > /etc/systemd/system/gms-heartbeat.service <<UNIT
[Unit]
Description=Report worker container health
After=docker.service
[Service]
Type=oneshot
EnvironmentFile=${APP_DIR}/worker/.env
Environment=BOX_ID=${BOX_ID:-box1}
ExecStart=/usr/bin/python3 ${APP_DIR}/worker/heartbeat.py
UNIT
cat > /etc/systemd/system/gms-heartbeat.timer <<UNIT
[Unit]
Description=Report worker health every minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=10s
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now gms-heartbeat.timer

cat <<DONE

==> READY (provisioned, not running). Now do the gated steps MANUALLY:

  1. Smoke-test ONE container:
       cd ${APP_DIR}/worker && docker compose up -d worker
       docker compose exec worker curl -s localhost:8080/health        # active_jobs=1, results_per_minute>0
       psql "\$DATABASE_URL" -c 'select count(*) from businesses;'      # rising via the shipper
  2. Scale to 12:
       docker compose up -d --scale worker_replica=11
  3. Calibrate per-container yield + proxy/bandwidth (FIRST_BOT_SETUP.md §4–6).

  Fleet: after box1 works, push gmapssaas:local to a registry, then on each new box
  run this script with IMAGE=<registry/image> to PULL instead of rebuilding.
DONE
