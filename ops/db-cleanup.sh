#!/usr/bin/env sh
# Archive lead_sources, drop it, and drop four confirmed-unused indexes.
#
# Runs inside the backup service container, which has PG* credentials and the
# host backups directory mounted:
#
#   ssh root@152.53.209.52
#   cd /srv/jawnix/app
#   docker compose run --rm \
#     -e DB_CLEANUP_CONFIRM=YES \
#     backup /app/ops/db-cleanup.sh
#
# Everything is idempotent: reruns skip objects that are already gone and
# reuse the same evidence directory when JAWNIX_DB_CLEANUP_RUN is pinned.
# Evidence, the compressed archive, and rollback notes land in
# /srv/jawnix/backups/db-cleanup-<timestamp>/ on the host.
set -eu

if [ "${DB_CLEANUP_CONFIRM:-}" != "YES" ]; then
  echo "Set DB_CLEANUP_CONFIRM=YES to archive lead_sources and drop it plus four indexes." >&2
  exit 2
fi

: "${PGHOST:?set PGHOST}"

run_id="${JAWNIX_DB_CLEANUP_RUN:-$(date -u +%Y%m%dT%H%M%SZ)}"
run_dir="/srv/jawnix/backups/db-cleanup-$run_id"
dump_path="$run_dir/lead-sources-$run_id.dump"
evidence="$run_dir/evidence.txt"
rollback="$run_dir/rollback.sql"
mkdir -p "$run_dir"

# Indexes confirmed unused by the 2026-11-11 audit (lifetime pg_stat counts,
# never reset). An index that has become hot since the audit is skipped.
DROP_INDEXES="
lead_inventory_state_age_idx
ix_lead_inventory_last_distributed_at
ix_distribution_events_agency_lead
ix_distribution_events_agent_lead
"
#: Lifetime idx_scan above which an index is treated as in use and kept.
scan_guard="${JAWNIX_DB_CLEANUP_SCAN_GUARD:-500}"
index_list="$(echo "$DROP_INDEXES" | tr ' ' '\n' | sed "s/^/'/;s/$/',/" | tr -d '\n' | sed 's/,$//')"

echo "== db-cleanup run $run_id =="
echo "evidence directory: $run_dir"

{
  echo "# db-cleanup $run_id evidence ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo
  echo "## lead_sources table stats"
  psql -x -c "SELECT * FROM pg_stat_user_tables WHERE relname='lead_sources'"
  echo
  echo "## candidate index stats"
  psql -x -c "
    SELECT indexrelname, pg_relation_size(indexrelid) AS bytes,
           idx_scan, idx_tup_read, idx_tup_fetch, pg_get_indexdef(indexrelid)
    FROM pg_stat_user_indexes
    WHERE indexrelname IN ($index_list)
       OR relname = 'lead_sources'"
  echo
  echo "## constraints depending on candidate indexes (expect none)"
  psql -x -c "
    SELECT i.indexrelname, c.conname, c.contype
    FROM pg_stat_user_indexes i JOIN pg_constraint c ON c.conindid = i.indexrelid
    WHERE i.indexrelname IN ($index_list)"
  echo
  echo "## foreign keys referencing lead_sources (expect none)"
  psql -x -c "SELECT conrelid::regclass, conname FROM pg_constraint WHERE confrelid='lead_sources'::regclass"
} > "$evidence" 2>&1 || true
echo "wrote $evidence"

# Record CREATE INDEX statements for rollback before dropping anything.
: > "$rollback"
for idx in $DROP_INDEXES; do
  def="$(psql --tuples-only --no-align -c "SELECT pg_get_indexdef('$idx'::regclass)" 2>/dev/null || true)"
  if [ -n "$def" ]; then
    printf '%s;\n' "$def" >> "$rollback"
  fi
done
if psql --tuples-only --no-align -c "SELECT to_regclass('public.lead_sources') IS NOT NULL" | grep -q t; then
  cat >> "$rollback" <<'EOF'
-- lead_sources: restore the table, its data, and its constraints from the
-- archive in this directory (pg_restore recreates the table when absent):
--   pg_restore --no-owner --no-acl --dbname="$PGDATABASE" lead-sources-<run>.dump
-- Then ANALYZE lead_sources;
EOF
fi
echo "wrote $rollback"

if psql --tuples-only --no-align -c "SELECT to_regclass('public.lead_sources') IS NOT NULL" | grep -q t; then
  echo "archiving lead_sources to $dump_path"
  pg_dump --format=custom --compress=9 --no-owner --no-acl \
    --table=lead_sources --file="$dump_path"
  sha256sum "$dump_path" > "$dump_path.sha256"
  rows="$(pg_restore --list "$dump_path" | grep -c 'TABLE DATA public lead_sources' || true)"
  [ "$rows" = "1" ] || { echo "archive is missing the lead_sources table data; aborting" >&2; exit 1; }
  echo "archive verified: $(du -h "$dump_path" | cut -f1), sha256 recorded"

  if [ -n "${RESTIC_REPOSITORY:-}" ] && [ -n "${RESTIC_PASSWORD:-}" ] && restic snapshots >/dev/null 2>&1; then
    restic backup --tag lead-sources-archive "$dump_path" "$dump_path.sha256"
    echo "archive snapshot in restic (tag lead-sources-archive)"
    echo "NOTE: the backup service prunes snapshots older than 14 days." \
      "The host file under $run_dir is the durable copy; copy it off-box if the" \
      "archive must outlive host retention."
  else
    echo "restic not configured in this environment; the host file is the only copy" >&2
  fi

  echo "dropping lead_sources"
  psql --single-transaction -c "DROP TABLE IF EXISTS public.lead_sources"
else
  echo "lead_sources already absent; skipping archive and drop"
fi

for idx in $DROP_INDEXES; do
  scans="$(psql --tuples-only --no-align -c "SELECT idx_scan FROM pg_stat_user_indexes WHERE indexrelname='$idx'" 2>/dev/null || true)"
  if [ -z "$scans" ]; then
    echo "$idx already absent; skipping"
    continue
  fi
  if [ "$scans" -gt "$scan_guard" ]; then
    echo "KEEPING $idx: idx_scan=$scans exceeds guard $scan_guard" >&2
    continue
  fi
  echo "dropping $idx (idx_scan=$scans)"
  psql -c "DROP INDEX CONCURRENTLY IF EXISTS public.$idx"
done

{
  echo
  echo "## post-cleanup sizes"
  psql -x -c "
    SELECT relname, pg_relation_size(relid) AS heap_bytes,
           pg_total_relation_size(relid) AS total_bytes
    FROM pg_stat_user_tables
    WHERE relname IN ('lead_inventory', 'distribution_events', 'jobs')"
  psql -c "SELECT to_regclass('public.lead_sources') AS lead_sources"
} >> "$evidence" 2>&1 || true

echo "== db-cleanup $run_id complete; evidence in $run_dir =="
