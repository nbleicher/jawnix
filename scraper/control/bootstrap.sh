#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# bootstrap.sh — install host control services on the all-in-one box
# ─────────────────────────────────────────────────────────────────────────────
# The control VPS is small (1–2 vCPU). It runs the control plane only — NOT
# browser workers, NOT the database (that's managed Postgres). It hosts:
#   • gmapssaas serve   — the queue API (POST /api/v1/scrape) + River maintenance
#   • gmapssaas admin   — the web control panel (jobs/workers, auth + 2FA)
#   • enqueue.py        — continuous top-up producer (systemd service)
#   • a daily timer     — global-combine lead export + keyword/campaign rollover
#   • monitor.py / stats.py / push target for keywords.txt
#
# Edit the CONFIG block, then:  sudo bash bootstrap.sh
set -euo pipefail

# ── CONFIG ───────────────────────────────────────────────────────────────────
APP_USER="scraper"
APP_DIR="${APP_DIR:-/home/${APP_USER}/scraper}"
SCRAPER_SRC="${SCRAPER_SRC:-${APP_DIR}/util/scraper_src}"
ENV_FILE="${ENV_FILE:-${APP_DIR}/.env.control}"
REPO_URL="${REPO_URL:-}"                  # optional: git URL to clone; else rsync the repo yourself
DATABASE_URL="${DATABASE_URL:-postgres://USER:PASS@127.0.0.1:5432/gmaps_pro?sslmode=disable}"
HASHID_SALT="${HASHID_SALT:-$(openssl rand -hex 16)}"
ENCRYPTION_KEY="${ENCRYPTION_KEY:-$(openssl rand -hex 32)}"
API_KEY="${API_KEY:-gms_$(openssl rand -hex 32)}"
GO_VERSION="1.26.2"
# ─────────────────────────────────────────────────────────────────────────────

echo "==> Creating app user and dirs"
id -u "${APP_USER}" &>/dev/null || useradd -m -s /bin/bash "${APP_USER}"
mkdir -p "${APP_DIR}" "${APP_DIR}/exports" "${APP_DIR}/logs"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "==> Installing system packages"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git rsync screen sudo \
    postgresql-client ufw fail2ban curl ca-certificates

echo "==> Installing Go ${GO_VERSION} (to build the gmapssaas binary)"
if ! command -v go &>/dev/null; then
  curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tgz
  rm -rf /usr/local/go && tar -C /usr/local -xzf /tmp/go.tgz
  echo 'export PATH=$PATH:/usr/local/go/bin' > /etc/profile.d/go.sh
fi
export PATH=$PATH:/usr/local/go/bin

echo "==> Fetching the repo into ${APP_DIR}"
if [ -n "${REPO_URL}" ]; then
  sudo -u "${APP_USER}" git clone "${REPO_URL}" "${APP_DIR}" || echo "  (already present)"
else
  echo "  REPO_URL not set — rsync the repo to ${APP_DIR} yourself, then re-run from '==> Python venv'."
fi

echo "==> Python venv + control-plane deps"
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/venv"
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install -q --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install -q paramiko pyyaml openpyxl psycopg2-binary httpx

echo "==> Building the gmapssaas binary (serve + admin + worker)"
if [ -d "${SCRAPER_SRC}" ]; then
  ( cd "${SCRAPER_SRC}" && /usr/local/go/bin/go build -o "${APP_DIR}/gmapssaas" ./cmd/gmapssaas )
  chown "${APP_USER}:${APP_USER}" "${APP_DIR}/gmapssaas"
fi

echo "==> Writing ${ENV_FILE} (chmod 600, never commit)"
cat > "${ENV_FILE}" <<ENV
DATABASE_URL=${DATABASE_URL}
HASHID_SALT=${HASHID_SALT}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
ALERT_WEBHOOK=${ALERT_WEBHOOK:-}
API_KEY=${API_KEY}
UPTIME_HEARTBEAT_URL=${UPTIME_HEARTBEAT_URL:-}
GMS_QUEUE_MIN_DEPTH=${GMS_QUEUE_MIN_DEPTH:-1}
GMS_QUEUE_MAX_DEPTH=${GMS_QUEUE_MAX_DEPTH:-500}
GMS_QUEUE_MAX_AGE_MINS=${GMS_QUEUE_MAX_AGE_MINS:-30}
GMS_MAX_RETRYABLE=${GMS_MAX_RETRYABLE:-50}
GMS_MAX_EMPTY_RATE=${GMS_MAX_EMPTY_RATE:-0.9}
GMS_MAX_SPOOL_FILES=${GMS_MAX_SPOOL_FILES:-200}
GMS_MAX_SPOOL_AGE_MINS=${GMS_MAX_SPOOL_AGE_MINS:-15}
GMS_DISK_WARN_PERCENT=${GMS_DISK_WARN_PERCENT:-85}
GMS_MEMORY_WARN_PERCENT=${GMS_MEMORY_WARN_PERCENT:-90}
GMS_TELEMETRY_STALE_SECS=${GMS_TELEMETRY_STALE_SECS:-180}
ENV
chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"; chmod 600 "${ENV_FILE}"

if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
echo "==> Running scraper database migrations (rubenv/sql-migrate CLI)"
sudo -u "${APP_USER}" bash -lc "GOBIN=${APP_DIR}/bin /usr/local/go/bin/go install github.com/rubenv/sql-migrate/...@latest" || true
cat > "${SCRAPER_SRC}/migrations/dbconfig.prod.yml" <<DBC
production:
  dialect: postgres
  datasource: ${DATABASE_URL}
  dir: migrations
  table: migrations
DBC
sudo -u "${APP_USER}" bash -lc "cd ${SCRAPER_SRC} && ${APP_DIR}/bin/sql-migrate up -config=migrations/dbconfig.prod.yml -env=production" || \
  echo "  (migrations failed — run manually per SETUP_POSTGRES_CONTROL_VPS.md Part A4)"
fi

echo "==> Creating the admin user and control-plane API key"
admin_password="${ADMIN_PASSWORD:-${DASH_PASSWORD:-$(openssl rand -hex 16)}}"
sudo -u "${APP_USER}" env DATABASE_URL="${DATABASE_URL}" ENCRYPTION_KEY="${ENCRYPTION_KEY}" \
  "${APP_DIR}/gmapssaas" admin create-user --username admin --password "${admin_password}"
api_key_hash="$(printf '%s' "${API_KEY}" | sha256sum | awk '{print $1}')"
api_key_prefix="${API_KEY:0:12}"
psql "${DATABASE_URL}" --set ON_ERROR_STOP=1 \
  --set api_key_hash="${api_key_hash}" --set api_key_prefix="${api_key_prefix}" <<'SQL'
INSERT INTO api_keys (user_id, name, key_hash, key_prefix)
SELECT id, 'control-plane', :'api_key_hash', :'api_key_prefix'
FROM users WHERE username = 'admin'
ON CONFLICT (key_hash) DO UPDATE SET revoked_at = NULL;
SQL

echo "==> systemd units"
cat > /etc/systemd/system/gms-serve.service <<UNIT
[Unit]
Description=gmapssaas queue API (serve)
After=network-online.target
[Service]
User=${APP_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/gmapssaas serve --addr :8080
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/gms-admin.service <<UNIT
[Unit]
Description=gmapssaas admin dashboard (control panel)
After=network-online.target
[Service]
User=${APP_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/gmapssaas admin --addr 127.0.0.1:8091
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/gms-enqueue.service <<UNIT
[Unit]
Description=Continuous top-up enqueuer
After=gms-serve.service
[Service]
User=${APP_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/control/enqueue.py --watch --keywords ${APP_DIR}/control/runtime/keywords.txt
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
UNIT

# Daily global-combine export + campaign rollover (08:00 UTC)
cat > /etc/systemd/system/gms-export.service <<UNIT
[Unit]
Description=Daily global-combine lead export
[Service]
Type=oneshot
User=${APP_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/control/export_leads.py --daily
UNIT
cat > /etc/systemd/system/gms-export.timer <<UNIT
[Unit]
Description=Run global-combine lead export daily
[Timer]
OnCalendar=*-*-* 08:00:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT

# Alerting (O-10) — push an alert if the pipeline drifts. Runs every 10 min.
# Tune thresholds/target; set ALERT_WEBHOOK in .env for Slack-style notifications.
cat > /etc/systemd/system/gms-alert.service <<UNIT
[Unit]
Description=Pipeline drift alerts
[Service]
Type=oneshot
User=${APP_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/control/alert.py --api-base http://localhost:8080
UNIT
cat > /etc/systemd/system/gms-alert.timer <<UNIT
[Unit]
Description=Run pipeline drift alerts every 10 min
[Timer]
OnBootSec=5min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
UNIT

mkdir -p "${APP_DIR}/control/triggers"
touch "${APP_DIR}/control/triggers/enqueue.request"
cat > /etc/systemd/system/enqueue-trigger.service <<UNIT
[Unit]
Description=Run one enqueue top-up requested by the dashboard
After=gms-serve.service
[Service]
Type=oneshot
User=${APP_USER}
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/control/enqueue.py --once
UNIT
cat > /etc/systemd/system/enqueue-trigger.path <<UNIT
[Unit]
Description=Watch for dashboard enqueue requests
[Path]
PathModified=${APP_DIR}/control/triggers/enqueue.request
Unit=enqueue-trigger.service
[Install]
WantedBy=multi-user.target
UNIT

echo "==> Firewall (allow SSH; admin/API only over the private net or a tunnel)"
ufw allow 22/tcp || true
ufw --force enable || true

systemctl daemon-reload
systemctl enable --now gms-serve.service gms-enqueue.service \
  gms-export.timer gms-alert.timer enqueue-trigger.path || \
  echo "  (enable services after confirming the binary subcommands)"

echo
echo "==> Done. Next:"
echo "   1. Start workers with docker-compose.box.yml; containers use the private db service hostname."
echo "   2. From your laptop:  python3 control/push_keywords.py keywords.txt --host <this-vps> --trigger-run"
echo "   3. Tunnel the dashboard: ssh -L 8090:127.0.0.1:8090 <box>"
