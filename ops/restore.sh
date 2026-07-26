#!/usr/bin/env sh
set -eu

if [ "${RESTORE_CONFIRM:-}" != "YES" ]; then
  echo "Set RESTORE_CONFIRM=YES to restore the selected dump." >&2
  exit 2
fi

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "usage: RESTORE_CONFIRM=YES restore.sh /path/to/jawnix.dump" >&2
  exit 2
fi

pg_restore --clean --if-exists --no-owner --no-acl --dbname="${PGDATABASE:-jawnix}" "$1"

if [ -n "${JAWNIX_SCRAPER_RESTORE_PATH:-}" ]; then
  if [ ! -f "$JAWNIX_SCRAPER_RESTORE_PATH" ]; then
    echo "Scraper Dataset restore file was not found." >&2
    exit 2
  fi
  restored_checksum="$(sha256sum "$JAWNIX_SCRAPER_RESTORE_PATH" | cut -d ' ' -f 1)"
  latest_checksum="$(
    psql --tuples-only --no-align --dbname="${PGDATABASE:-jawnix}" \
      --command="SELECT checksum FROM migration_audits ORDER BY completed_at DESC LIMIT 1"
  )"
  if [ -n "$latest_checksum" ] && [ "$restored_checksum" != "$latest_checksum" ]; then
    previously_synchronized="$(
      psql --tuples-only --no-align --dbname="${PGDATABASE:-jawnix}" \
        --command="SELECT COUNT(*) FROM migration_audits WHERE checksum = '$restored_checksum'"
    )"
    if [ "$previously_synchronized" != "0" ]; then
      echo "Refusing a Scraper Dataset that predates PostgreSQL's last synchronized version." >&2
      exit 2
    fi
  fi
  install -m 0640 "$JAWNIX_SCRAPER_RESTORE_PATH" /data/health_leads/data/leads.db
  echo "Restored Scraper Dataset checksum $restored_checksum; run sync-scrapers to replay newer data."
fi
