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
  if [ -z "${JAWNIX_SCRAPER_RESTORE_METADATA_PATH:-}" ] || \
     [ ! -f "$JAWNIX_SCRAPER_RESTORE_METADATA_PATH" ]; then
    echo "Scraper Dataset restore metadata was not found." >&2
    exit 2
  fi
  python -m jawnix_data restore-scraper-dataset \
    --dataset "$JAWNIX_SCRAPER_RESTORE_PATH" \
    --metadata "$JAWNIX_SCRAPER_RESTORE_METADATA_PATH" \
    --apply
fi
