#!/usr/bin/env sh
# Prepare and (with --apply) execute the legacy host decommission.
#
# Legacy host: 159.195.15.51 (ssh alias `jawnix-legacy`, key ~/.ssh/jawnix_vps).
# It still runs `jawnix-postgres-1` (the pre-migration 15 GB `jawnix` database)
# and `jawnix-caddy` (no published ports; buzz-prod-caddy-1 owns 80/443).
#
# Default is a dry run: read-only evidence collection only, nothing changes.
# `--apply` performs, in order:
#   1. final custom-format pg_dump on the legacy host
#   2. SHA-256 + `pg_restore --list` verification on the legacy host
#   3. copy of the dump to the production host backup dir (streamed through
#      this machine), with a destination checksum comparison
#   4. stop and removal of the jawnix-postgres-1 and jawnix-caddy containers
#      and the already-stopped jawnix app containers (buzz-prod-* is never
#      touched; Docker volumes are left intact for manual cleanup)
#   5. printed instructions for deleting the metrics.jawnix.com DNS record
#
# usage: ops/legacy-decommission.sh [--apply]
set -eu

fail() {
  echo "$1" >&2
  exit 2
}

apply=false
if [ "${1:-}" = "--apply" ]; then
  apply=true
elif [ -n "${1:-}" ]; then
  fail "usage: ops/legacy-decommission.sh [--apply]"
fi

legacy_target="${JAWNIX_LEGACY_SSH_TARGET:-jawnix-legacy}"
prod_target="${JAWNIX_DEPLOY_TARGET:-root@152.53.209.52}"
prod_backup_dir="${JAWNIX_PROD_BACKUP_DIR:-/srv/jawnix/backups}"
legacy_dump_dir="${JAWNIX_LEGACY_DUMP_DIR:-/srv/jawnix/backups}"
pg_container="${JAWNIX_LEGACY_PG_CONTAINER:-jawnix-postgres-1}"
caddy_container="${JAWNIX_LEGACY_CADDY_CONTAINER:-jawnix-caddy}"
pg_user="${JAWNIX_LEGACY_PG_USER:-jawnix}"
pg_db="${JAWNIX_LEGACY_PG_DB:-jawnix}"

# Stopped legacy app containers from the retired stack. buzz-prod-* belongs to
# the other tenant on this shared box and must never appear here.
stale_containers="jawnix-api-1 jawnix-worker-1 jawnix-scheduler-1 jawnix-metrics-worker-1 jawnix-backup-1 jawnix-migrate-1"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_name="jawnix-legacy-final-$timestamp.dump"
legacy_dump="$legacy_dump_dir/$dump_name"
prod_dump="$prod_backup_dir/$dump_name"

legacy_ssh() {
  ssh -o BatchMode=yes "$legacy_target" "$@"
}

prod_ssh() {
  ssh -o BatchMode=yes "$prod_target" "$@"
}

echo "== Legacy host decommission ($([ "$apply" = true ] && echo APPLY || echo DRY-RUN)) =="
echo "legacy host: $legacy_target"
echo "production host: $prod_target"
echo "dump name: $dump_name"
echo

echo "-- Evidence: database connections (excluding this probe) --"
connections="$(legacy_ssh "docker exec $pg_container psql -U $pg_user -d $pg_db -tAc \
  \"SELECT count(*) FROM pg_stat_activity WHERE datname = '$pg_db' AND pid <> pg_backend_pid();\"")"
echo "active connections to $pg_db: $connections"
if [ "$apply" = true ] && [ "$connections" != "0" ]; then
  fail "Refusing to decommission: $connections connection(s) still open on $pg_db."
fi

echo "-- Evidence: database sizes --"
legacy_ssh "docker exec $pg_container psql -U $pg_user -d $pg_db -tAc \
  \"SELECT datname, pg_size_pretty(pg_database_size(datname)) FROM pg_database ORDER BY pg_database_size(datname) DESC;\""

echo "-- Evidence: published ports (expect none on jawnix-caddy / jawnix-postgres-1) --"
legacy_ssh "docker ps --filter name=jawnix --format '{{.Names}} {{.Ports}}'"

echo "-- Evidence: jawnix-caddy Caddyfile --"
legacy_ssh "docker exec $caddy_container cat /etc/caddy/Caddyfile"

if [ "$apply" != true ]; then
  cat <<EOF

Dry run only. With --apply this script will:
  1. docker exec $pg_container pg_dump -U $pg_user -d $pg_db --format=custom --no-owner --no-acl > $legacy_dump
  2. sha256sum + pg_restore --list the dump on the legacy host
  3. stream $legacy_dump to $prod_target:$prod_dump and compare SHA-256
  4. verify pg_restore --list of the copied dump on the production host
  5. docker stop/rm $pg_container and $caddy_container, and rm the stopped
     containers: $stale_containers
  6. remove the dump file left on the legacy host
  7. print the Cloudflare DNS steps for deleting metrics.jawnix.com
Docker volumes (jawnix_postgres_data, jawnix_caddy_data, jawnix_caddy_config)
are NOT removed; do that manually only after the dump copy is verified.
EOF
  exit 0
fi

echo "-- Step 1: final pg_dump on the legacy host --"
legacy_ssh "mkdir -p $legacy_dump_dir && \
  docker exec $pg_container pg_dump -U $pg_user -d $pg_db --format=custom --no-owner --no-acl > $legacy_dump"

echo "-- Step 2: checksum and integrity check on the legacy host --"
legacy_sum="$(legacy_ssh "sha256sum $legacy_dump" | awk '{print $1}')"
echo "legacy sha256: $legacy_sum"
legacy_ssh "docker exec -i $pg_container pg_restore --list < $legacy_dump > /dev/null"
echo "pg_restore --list: OK"

echo "-- Step 3: copy dump to the production host backup dir --"
legacy_ssh "cat $legacy_dump" | prod_ssh "mkdir -p $prod_backup_dir && cat > $prod_dump"
prod_sum="$(prod_ssh "sha256sum $prod_dump" | awk '{print $1}')"
echo "production sha256: $prod_sum"
if [ "$prod_sum" != "$legacy_sum" ]; then
  fail "Checksum mismatch between $legacy_dump and $prod_dump; aborting before any removal."
fi

echo "-- Step 4: verify the copied dump on the production host --"
prod_ssh "docker run --rm -i postgres:18 pg_restore --list < $prod_dump > /dev/null"
echo "production pg_restore --list: OK"
echo "Final dump preserved at $prod_target:$prod_dump (sha256 $prod_sum)"

echo "-- Step 5: stop and remove the legacy jawnix containers --"
legacy_ssh "docker stop $caddy_container $pg_container"
legacy_ssh "docker rm $caddy_container $pg_container $stale_containers"

echo "-- Step 6: remove the dump file left on the legacy host --"
legacy_ssh "rm -f $legacy_dump"

cat <<'EOF'

-- Step 7: delete the metrics.jawnix.com DNS record (manual, Cloudflare) --
  1. Open https://dash.cloudflare.com and select the jawnix.com zone.
  2. Go to DNS > Records.
  3. Find the A record: name `metrics`, content 159.195.15.51.
  4. Click Edit, then Delete, and confirm.
  5. Confirm `dig +short metrics.jawnix.com` returns nothing.
No other record points at 159.195.15.51; jawnix.com, www, staging, and
scraper.jawnix.com already resolve to the production host 152.53.209.52.

Remaining manual steps are in docs/legacy-host-decommission.md.
EOF
