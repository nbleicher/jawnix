#!/usr/bin/env bash
# One-time production host cleanup for /srv/jawnix, prepared 2026-11-12 against
# host "textro" (152.53.209.52). Removes:
#   1. the 39G September base backup backups/base-20260913T040000Z (superseded;
#      current database, WAL, and dataset snapshots are in the Restic repository),
#   2. rollback/release images older than the newest 3 releases (release-activate.py
#      prunes these on every activation; this is the one-time catch-up),
#   3. the Docker build cache (19.7G) via docker builder prune,
#   4. the 50 one-off release scripts, tarballs, logs, and staging folders listed
#      below (all verified present on 2026-11-12 and superseded by the merged
#      release-prepare.py/release-activate.py flow).
# Idempotent: every removal checks existence first and skips what is already gone.
# Default is a dry run that only reports; pass --apply to execute.
set -euo pipefail

fail() {
  echo "$1" >&2
  exit 2
}

ROOT=/srv/jawnix
KEEP_RELEASES=3
BASE_BACKUP="$ROOT/backups/base-20260913T040000Z"

apply=0
case "${1:-}" in
  "") ;;
  --apply) apply=1 ;;
  *) fail "usage: ops/host-cleanup.sh [--apply]   (default: dry run)" ;;
esac

[ -d "$ROOT" ] || fail "Run this on the production host: $ROOT does not exist."
command -v docker >/dev/null 2>&1 || fail "docker is required."

removed=0
skipped=0

remove() {
  if [ -e "$1" ]; then
    if [ "$apply" -eq 1 ]; then
      rm -rf -- "$1"
      echo "removed:  $1"
    else
      echo "would remove: $1"
    fi
    removed=$((removed + 1))
  else
    echo "absent, skipping: $1"
    skipped=$((skipped + 1))
  fi
}

echo "== 1. September base backup =="
# Guards: the exact verified path must be a directory containing base.tar.gz and
# older than 14 days, so a fresh base backup can never match by accident.
if [ -d "$BASE_BACKUP" ] && [ -f "$BASE_BACKUP/base.tar.gz" ]; then
  if [ -n "$(find "$BASE_BACKUP" -maxdepth 0 -mtime +14)" ]; then
    remove "$BASE_BACKUP"
  else
    fail "$BASE_BACKUP is newer than 14 days; refusing to remove it."
  fi
else
  echo "absent, skipping: $BASE_BACKUP"
  skipped=$((skipped + 1))
fi

echo "== 2. Rollback/release images older than the newest $KEEP_RELEASES releases =="
kept_shas="$(ls -1dt "$ROOT"/backups/release-* 2>/dev/null | xargs -r -n1 basename \
  | sed -n 's/^release-\([0-9a-f]\{7\}\)$/\1/p' | head -n "$KEEP_RELEASES")"
[ -n "$kept_shas" ] || fail "No releases found under $ROOT/backups; refusing to prune rollback images."
echo "keeping releases: $(echo "$kept_shas" | tr '\n' ' ')"

remove_image() {
  if [ "$apply" -eq 1 ]; then
    if docker image rm "$1" >/dev/null; then echo "removed:  $1"; else echo "FAILED:   $1" >&2; fi
  else
    echo "would remove: $1"
  fi
  removed=$((removed + 1))
}
for tag in $(docker images --format '{{.Repository}}:{{.Tag}}'); do
  repo="${tag%%:*}"
  name="${tag##*:}"
  case "$repo:$name" in
    jawnix-release:*)
      if echo "$name" | grep -qE '^[0-9a-f]{7}$' && ! echo "$kept_shas" | grep -qx "$name"; then
        remove_image "$tag"
      fi
      continue
      ;;
  esac
  case "$repo" in
    *rollback*) ;; # legacy one-off rollback images, e.g. jawnix-alex-release-*-rollback:latest
    *)
      case "$name" in
        rollback-*) ;;
        *) continue ;;
      esac
      ;;
  esac
  sha="${name#rollback-}"
  if echo "$sha" | grep -qE '^[0-9a-f]{7}$' && echo "$kept_shas" | grep -qx "$sha"; then
    continue
  fi
  remove_image "$tag"
done

echo "== 3. Docker build cache =="
docker system df | grep -E '^(TYPE|Build Cache)'
if [ "$apply" -eq 1 ]; then
  docker builder prune -af
else
  echo "would run: docker builder prune -af"
fi

echo "== 4. One-off release scripts, tarballs, logs, and staging folders =="
for path in \
  activate-release.py \
  prepare-release.py \
  build-production-20261012T143245Z.log \
  catalogue-stage-66165ac \
  csv-download-178814a.tar.gz \
  csv-download-activate.py \
  csv-download-prepare.py \
  csv-download-result.json \
  csv-download-stage-178814a \
  customer-reset-998686e.tar \
  customer-reset-backfill.py \
  customer-reset-fe012e8.tar \
  customer-reset-postgres-check.py \
  customer-reset-stage-998686e \
  customer-reset-stage-fe012e8 \
  customer-reset-verify.py \
  keyword-stage-8372720 \
  keyword-stage-ac1f3c8 \
  KEYWORD_DEPLOYED_COMMIT \
  main-post-migrate.py \
  o1-o2-main-build.log \
  o1-status-main-build.log \
  operator-uploads-1590d3c.tar.gz \
  operator-uploads-activate.py \
  operator-uploads-prepare.py \
  operator-uploads-stage-1590d3c \
  review-recovery-activate.py \
  review-recovery-source.tar \
  review-recovery-stage-81f023b \
  signup-477a061.tar \
  signup-477a061.tar.gz \
  signup-activate-grid-release.py \
  signup-grid-90016ce.tar.gz \
  signup-grid-stage-90016ce \
  signup-prepare-grid-release.py \
  signup-prepare-release.py \
  signup-stage-477a061 \
  signup-states-397ba62.tar.gz \
  signup-states-stage-397ba62 \
  stripe-db1-32a13e1.tar.gz \
  stripe-db1-768e364.tar.gz \
  stripe-db1-activate.py \
  stripe-db1-dfe31d4.tar.gz \
  stripe-db1-prepare.py \
  stripe-db1-stage-32a13e1 \
  stripe-db1-stage-768e364 \
  ui-overhaul-30bede3.tar.gz \
  ui-overhaul-activate.py \
  ui-overhaul-prepare.py \
  ui-overhaul-stage-30bede3
do
  remove "$ROOT/$path"
done

echo
if [ "$apply" -eq 1 ]; then
  echo "Done. $removed item(s) removed, $skipped already absent."
  df -h /
else
  echo "Dry run: $removed item(s) would be removed, $skipped already absent. Rerun with --apply to execute."
fi
