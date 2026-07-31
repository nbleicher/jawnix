#!/bin/bash
# ============================================================
# Google Maps Scraper — Worker Setup
# ============================================================
# Run this on any fresh Ubuntu/Debian VPS to install Docker
# and start a scraper worker connected to your central DB.
#
# Usage (on the VPS):
#   bash worker-setup.sh
#
# Remote one-liner (if you host this file somewhere):
#   ssh root@<new-vps> 'bash <(curl -fsSL https://yourhost/worker-setup.sh)'
#
# Fill in the three variables below — everything else is automatic.
# ============================================================

set -euo pipefail

# ---- YOUR SETTINGS (edit these once) -----------------------

DATABASE_URL="postgres://noah:<password>@ep-xxxx.us-east-1.aws.neon.tech/gmapssaas?sslmode=require"
ENCRYPTION_KEY="your_32_byte_hex_key_here"
CONCURRENCY=4          # workers to run (half of available vCores is a safe start)
FAST_MODE=true         # stealth HTTP mode — recommended for VPS
MAX_JOBS_PER_CYCLE=100 # restart worker after this many jobs (memory hygiene)
PROXIES=""             # optional: "http://user:pass@proxy1:8080,http://..."
IMAGE="ghcr.io/gosom/google-maps-scraper-saas:latest"

# ------------------------------------------------------------

info()  { echo -e "\033[1;32m✓\033[0m $1"; }
fail()  { echo -e "\033[1;31m✗\033[0m $1" >&2; exit 1; }

[[ "$DATABASE_URL" == *"<password>"* ]] && fail "Set your DATABASE_URL before running this script."
[[ "$ENCRYPTION_KEY" == "your_32_byte_hex_key_here" ]] && fail "Set your ENCRYPTION_KEY before running this script."

# --- detect arch for log only (image is multi-arch) ---
ARCH=$(uname -m)
info "Architecture: $ARCH"

# --- install docker if needed ---
if ! command -v docker &>/dev/null; then
    info "Installing Docker..."
    export DEBIAN_FRONTEND=noninteractive
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    info "Docker installed"
else
    info "Docker already installed: $(docker --version)"
fi

# --- create worker directory ---
mkdir -p /opt/gms-worker
chmod 700 /opt/gms-worker

# --- write .env ---
cat > /opt/gms-worker/.env << ENVEOF
DATABASE_URL=${DATABASE_URL}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
CONCURRENCY=${CONCURRENCY}
MAX_JOBS_PER_CYCLE=${MAX_JOBS_PER_CYCLE}
FAST_MODE=${FAST_MODE}
PROXIES=${PROXIES}
ENVEOF
chmod 600 /opt/gms-worker/.env
info "Environment file written"

# --- write docker-compose.yml ---
cat > /opt/gms-worker/docker-compose.yml << COMPOSEEOF
services:
  worker:
    image: ${IMAGE}
    restart: unless-stopped
    ports:
      - "8081:8080"
    env_file:
      - .env
    command: ["worker"]
    mem_limit: "2g"
    shm_size: "1g"
    tmpfs:
      - /tmp:size=512m
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
COMPOSEEOF
info "docker-compose.yml written"

# --- pull image and start ---
info "Pulling image (this may take a minute)..."
cd /opt/gms-worker
docker compose pull
docker compose up -d
info "Worker started"

# --- verify ---
sleep 5
if docker compose ps | grep -q "running\|Up"; then
    info "Worker is running"
    docker compose ps
else
    fail "Worker failed to start — check: docker compose -f /opt/gms-worker/docker-compose.yml logs"
fi

echo ""
echo "============================================================"
echo "  Worker is running at http://$(hostname -I | awk '{print $1}'):8081/health"
echo "  Logs: docker compose -C /opt/gms-worker logs -f"
echo "  Stop: docker compose -C /opt/gms-worker down"
echo "============================================================"
