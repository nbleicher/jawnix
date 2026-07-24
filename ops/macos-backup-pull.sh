#!/bin/zsh
set -eu

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

external_root="${JAWNIX_EXTERNAL_BACKUP_ROOT:-/Volumes/Peely SSD/Jawnix Backups}"
ssh_target="${JAWNIX_VPS_SSH_TARGET:-root@159.195.15.51}"
source_repository="${JAWNIX_VPS_RESTIC_REPOSITORY:-/srv/jawnix/restic-repository}"
retention_days="${JAWNIX_EXTERNAL_BACKUP_RETENTION_DAYS:-14}"
telegram_chat_id="${TELEGRAM_CHAT_ID:-6775236603}"
source_password_service="${JAWNIX_SOURCE_RESTIC_KEYCHAIN_SERVICE:-jawnix-vps-restic-password}"
external_password_service="${JAWNIX_EXTERNAL_RESTIC_KEYCHAIN_SERVICE:-jawnix-external-restic-password}"
telegram_service="${JAWNIX_TELEGRAM_KEYCHAIN_SERVICE:-jawnix-telegram-bot-token}"
keychain_account="${JAWNIX_KEYCHAIN_ACCOUNT:-$USER}"

notify_failure() {
  message="$1"
  token="$(security find-generic-password -a "$keychain_account" -s "$telegram_service" -w 2>/dev/null || true)"
  if [ -n "$token" ]; then
    curl -fsS \
      -X POST \
      -H "Content-Type: application/json" \
      --data "$(jq -n --arg chat "$telegram_chat_id" --arg text "Jawnix external backup failed: $message" '{chat_id:$chat,text:$text}')" \
      "https://api.telegram.org/bot${token}/sendMessage" >/dev/null 2>&1 || true
  fi
}

fail() {
  notify_failure "$1"
  print -u2 -- "$1"
  exit 1
}

command -v restic >/dev/null 2>&1 || fail "restic is not installed on the Mac."
[ -d "/Volumes/Peely SSD" ] || fail "Peely SSD is not mounted."
mkdir -p "$external_root"

task_tmp="$(mktemp -d)"
trap 'rm -rf "$task_tmp"' EXIT

security find-generic-password -a "$keychain_account" -s "$source_password_service" -w \
  >"$task_tmp/source-password" 2>/dev/null || fail "VPS Restic password is missing from Keychain."
security find-generic-password -a "$keychain_account" -s "$external_password_service" -w \
  >"$task_tmp/external-password" 2>/dev/null || fail "External-drive Restic password is missing from Keychain."
chmod 600 "$task_tmp/source-password" "$task_tmp/external-password"

destination_repository="$external_root/restic-repository"
if ! RESTIC_PASSWORD_FILE="$task_tmp/external-password" restic -r "$destination_repository" snapshots >/dev/null 2>&1; then
  RESTIC_PASSWORD_FILE="$task_tmp/external-password" restic -r "$destination_repository" init
fi

RESTIC_PASSWORD_FILE="$task_tmp/external-password" \
  restic -r "$destination_repository" copy \
  --from-repo "sftp:${ssh_target}:${source_repository}" \
  --from-password-file "$task_tmp/source-password"

RESTIC_PASSWORD_FILE="$task_tmp/external-password" \
  restic -r "$destination_repository" forget --keep-within "${retention_days}d" --prune

RESTIC_PASSWORD_FILE="$task_tmp/external-password" \
  restic -r "$destination_repository" check

print -- "Jawnix external backup completed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
