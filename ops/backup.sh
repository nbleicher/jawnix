#!/usr/bin/env sh
set -eu

: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD:?set RESTIC_PASSWORD}"
: "${PGHOST:?set PGHOST}"

backup_dir=/srv/jawnix/backups
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_path="$backup_dir/jawnix-$timestamp.dump"
db2_dump_path="$backup_dir/jawnix-db2-$timestamp.dump"
base_dir="$backup_dir/base-$timestamp"
wal_dir=/var/lib/postgresql/data/pgdata/wal-archive
scraper_data=/data/health_leads/data
scraper_dataset="$scraper_data/leads.db"
scraper_metadata="$scraper_data/dataset-metadata.json"
scraper_checksum="$backup_dir/scraper-dataset.sha256"
mkdir -p "$backup_dir"

pg_dump --format=custom --no-owner --no-acl --file="$dump_path"
include_db2=false
if [ "$(psql --tuples-only --no-align --command "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='jawnix_db2')")" = "t" ]; then
  pg_dump --dbname=jawnix_db2 --format=custom --no-owner --no-acl --file="$db2_dump_path"
  include_db2=true
fi

include_base=false
base_day="${JAWNIX_BASEBACKUP_DAY_UTC:-0}"
if [ "${JAWNIX_FORCE_BASEBACKUP:-false}" = "true" ] || [ "$(date -u +%w)" = "$base_day" ]; then
  mkdir -p "$base_dir"
  pg_basebackup --pgdata="$base_dir" --format=tar --gzip --wal-method=stream --checkpoint=fast
  include_base=true
fi

if ! restic snapshots >/dev/null 2>&1; then
  restic init
fi

set -- "$dump_path"
if [ "$include_db2" = "true" ]; then
  set -- "$@" "$db2_dump_path"
fi
if [ "$include_base" = "true" ]; then
  set -- "$@" "$base_dir"
fi
restic backup --tag database "$@"
restic backup --tag wal "$wal_dir"
if [ -d /srv/jawnix/batches/lead-uploads ]; then
  restic backup --tag lead-uploads /srv/jawnix/batches/lead-uploads
fi
if [ -f "$scraper_dataset" ]; then
  sha256sum "$scraper_dataset" > "$scraper_checksum"
  if [ -f "$scraper_metadata" ]; then
    restic backup --tag scraper-dataset "$scraper_data"
  else
    echo "Scraper Dataset version metadata is missing; preserving the legacy dataset with its checksum: $scraper_metadata" >&2
    restic backup \
      --tag scraper-dataset \
      --tag legacy-metadata-missing \
      "$scraper_data"
  fi
  restic backup --tag scraper-dataset "$scraper_checksum"
else
  echo "Persistent Scraper Dataset is missing: $scraper_dataset" >&2
  exit 1
fi
restic forget --group-by tags --keep-within 14d --prune
rm -f "$dump_path"
if [ "$include_db2" = "true" ]; then
  rm -f "$db2_dump_path"
fi
if [ "$include_base" = "true" ]; then
  rm -rf "$base_dir"
fi
find "$wal_dir" -type f -mtime +14 -delete
