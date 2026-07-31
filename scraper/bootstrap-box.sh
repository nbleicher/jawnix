#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/gms-scale}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRAPER_SRC="${SCRAPER_SRC:-/opt/gms-scraper}"
SCRAPER_REPO="${SCRAPER_REPO:-git@github.com:nbleicher/scraper.git}"
SCRAPER_REVISION="${SCRAPER_REVISION:-7caca2ce8122c0ffaf47ca5737a06d05a23a90ca}"
SPOOL_DIR="${SPOOL_DIR:-/data/incoming}"
if [ "${EUID}" -ne 0 ]; then
  echo "Run as root: sudo APP_DIR=${APP_DIR} bash $0" >&2
  exit 2
fi

apt-get update -y
apt-get install -y docker.io curl ca-certificates python3 python3-venv python3-pip postgresql-client rsync openssl
if apt-cache show docker-compose-plugin >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin
else
  apt-get install -y docker-compose-v2
fi
systemctl enable --now docker
id -u scraper >/dev/null 2>&1 || useradd -m -s /bin/bash scraper
usermod -aG docker scraper
export SCRAPER_SRC
export APP_UID="${APP_UID:-$(id -u scraper)}"
export APP_GID="${APP_GID:-$(id -g scraper)}"
cpu_count="$(nproc)"
default_worker_replicas="$((cpu_count * 2 / 3))"
if [ "${default_worker_replicas}" -lt 1 ]; then
  default_worker_replicas=1
fi
export WORKER_REPLICAS="${WORKER_REPLICAS:-${default_worker_replicas}}"
if [ "${SOURCE_DIR}" != "${APP_DIR}" ]; then
  mkdir -p "${APP_DIR}"
  rsync -a --exclude .git --exclude web/node_modules "${SOURCE_DIR}/" "${APP_DIR}/"
fi
SCRAPER_REPO="${SCRAPER_REPO}" SCRAPER_REVISION="${SCRAPER_REVISION}" \
  "${SOURCE_DIR}/scripts/fetch-scraper-source.sh" "${SCRAPER_SRC}"
cd "${APP_DIR}"

if [ ! -f .env ]; then
  umask 077
  postgres_password="$(openssl rand -hex 24)"
  ro_password="$(openssl rand -hex 24)"
  hashid_salt="$(openssl rand -hex 16)"
  encryption_key="$(openssl rand -hex 32)"
  api_key="gms_$(openssl rand -hex 32)"
  scraper_control_token="$(openssl rand -hex 32)"
  cat >.env <<ENV
POSTGRES_USER=postgres
POSTGRES_PASSWORD=${postgres_password}
POSTGRES_DB=gmaps_pro
POSTGRES_PORT=5432
GMAPS_RO_PASSWORD=${ro_password}
HASHID_SALT=${hashid_salt}
ENCRYPTION_KEY=${encryption_key}
API_KEY=${api_key}
JAWNIX_SCRAPER_CONTROL_TOKEN=${scraper_control_token}
SCRAPER_CONTROL_BIND_ADDRESS=10.77.0.2
PROXIES=
SPOOL_DIR=${SPOOL_DIR}
SCRAPER_SRC=${SCRAPER_SRC}
SCRAPER_REPO=${SCRAPER_REPO}
SCRAPER_REVISION=${SCRAPER_REVISION}
APP_UID=${APP_UID}
APP_GID=${APP_GID}
WORKER_REPLICAS=${WORKER_REPLICAS}
UPTIME_HEARTBEAT_URL=
GMS_QUEUE_MIN_DEPTH=1
GMS_QUEUE_MAX_DEPTH=500
GMS_QUEUE_MAX_AGE_MINS=30
GMS_MAX_RETRYABLE=50
GMS_MAX_EMPTY_RATE=0.9
GMS_MAX_SPOOL_FILES=200
GMS_MAX_SPOOL_AGE_MINS=15
GMS_DISK_WARN_PERCENT=85
GMS_MEMORY_WARN_PERCENT=90
GMS_TELEMETRY_STALE_SECS=180
ENV
  chmod 600 .env
  printf '\nGenerated credentials (store these now):\n  JAWNIX_SCRAPER_CONTROL_TOKEN=%s\n  POSTGRES_PASSWORD=%s\n  GMAPS_RO_PASSWORD=%s\n  HASHID_SALT=%s\n\n' "$scraper_control_token" "$postgres_password" "$ro_password" "$hashid_salt"
else
  echo "Using existing ${APP_DIR}/.env"
fi

if ! grep -q '^API_KEY=' .env; then
  printf 'API_KEY=gms_%s\n' "$(openssl rand -hex 32)" >>.env
fi
if ! grep -q '^JAWNIX_SCRAPER_CONTROL_TOKEN=' .env; then
  printf 'JAWNIX_SCRAPER_CONTROL_TOKEN=%s\n' "$(openssl rand -hex 32)" >>.env
  echo "Added JAWNIX_SCRAPER_CONTROL_TOKEN to ${APP_DIR}/.env; store it with the Jawnix host secrets."
fi
if ! grep -q '^SCRAPER_CONTROL_BIND_ADDRESS=' .env; then
  printf 'SCRAPER_CONTROL_BIND_ADDRESS=10.77.0.2\n' >>.env
fi
set -a
source .env
set +a
mkdir -p "${SPOOL_DIR}/archive" control/triggers control/runtime exports/by_state
if [[ ! -f control/runtime/keywords.txt ]]; then
  if [[ -f keywords.txt ]]; then
    cp -p keywords.txt control/runtime/keywords.txt
  else
    touch control/runtime/keywords.txt
  fi
fi
if [[ "$(readlink keywords.txt 2>/dev/null || true)" != "control/runtime/keywords.txt" ]]; then
  rm -f keywords.txt
  ln -s control/runtime/keywords.txt keywords.txt
fi
chown -R scraper:scraper "${SPOOL_DIR}" control exports keywords.txt
chmod o+x "$(dirname "${SPOOL_DIR}")"
chmod 0750 "${SPOOL_DIR}"
chmod +x docker/postgres-init/010-readonly.sh docker/run-migrations.sh
docker compose -f docker-compose.box.yml up -d db
docker compose -f docker-compose.box.yml --profile tools run --rm migrate
APP_DIR="${APP_DIR}" SCRAPER_SRC="${SCRAPER_SRC}" SKIP_MIGRATIONS=1 \
  DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}?sslmode=disable" \
  HASHID_SALT="${HASHID_SALT}" ENCRYPTION_KEY="${ENCRYPTION_KEY}" \
  bash control/bootstrap.sh

cat >/etc/systemd/system/gms-ship.service <<UNIT
[Unit]
Description=Ship spooled scraper results to Postgres
[Service]
Type=oneshot
User=scraper
EnvironmentFile=${APP_DIR}/.env.control
Environment=GMS_SPOOL_DIR=${SPOOL_DIR}
WorkingDirectory=${APP_DIR}
ExecStart=/bin/bash -c 'while find "\$GMS_SPOOL_DIR" -maxdepth 1 -name "*.ndjson.done" -print -quit | grep -q .; do ${APP_DIR}/venv/bin/python ${APP_DIR}/worker/shipper.py --drain; sleep 2; done'
UNIT
cat >/etc/systemd/system/gms-ship.path <<UNIT
[Unit]
Description=Trigger the shipper when a completed spool file appears
[Path]
PathExistsGlob=${SPOOL_DIR}/*.ndjson.done
Unit=gms-ship.service
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/gms-ship.timer <<UNIT
[Unit]
Description=Backstop drain of the scraper spool
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
UNIT

cat >/etc/systemd/system/gms-heartbeat.service <<UNIT
[Unit]
Description=Report worker container health
After=docker.service
[Service]
Type=oneshot
User=scraper
EnvironmentFile=${APP_DIR}/.env.control
Environment=BOX_ID=%H
Environment=EXPECTED_WORKERS=${WORKER_REPLICAS}
Environment=GMS_SPOOL_DIR=${SPOOL_DIR}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/worker/heartbeat.py
UNIT
cat >/etc/systemd/system/gms-heartbeat.timer <<UNIT
[Unit]
Description=Report worker health every minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=10s
[Install]
WantedBy=timers.target
UNIT

cat >/etc/systemd/system/gms-uptime.service <<UNIT
[Unit]
Description=Report stack availability to external heartbeat
After=network-online.target docker.service
[Service]
Type=oneshot
User=scraper
EnvironmentFile=${APP_DIR}/.env.control
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/control/uptime_probe.py
UNIT
cat >/etc/systemd/system/gms-uptime.timer <<UNIT
[Unit]
Description=Check local stack and report external uptime every minute
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl reset-failed gms-ship.path gms-ship.service gms-alert.service gms-uptime.service || true
systemctl enable --now gms-ship.path gms-ship.timer gms-heartbeat.timer gms-uptime.timer

docker compose -f docker-compose.box.yml up -d --build --scale worker="${WORKER_REPLICAS}" scraper-control worker
echo "Workers: ${WORKER_REPLICAS} replicas (override with WORKER_REPLICAS in ${APP_DIR}/.env)"
echo "Scraper control: http://${SCRAPER_CONTROL_BIND_ADDRESS}:8090/api/workspace (WireGuard only)"
