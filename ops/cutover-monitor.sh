#!/bin/sh
set -u

app_dir="${JAWNIX_APP_DIR:-/srv/jawnix/app}"
state_dir="${JAWNIX_MONITOR_STATE_DIR:-/srv/jawnix/monitoring}"
state_file="$state_dir/last-status"
mkdir -p "$state_dir"

ready_body="$(curl -fsS --max-time 20 https://jawnix.com/api/readyz 2>/dev/null || true)"
health_body="$(curl -fsS --max-time 20 https://jawnix.com/api/healthz 2>/dev/null || true)"
running_services="$(
  cd "$app_dir" &&
    docker compose -f docker-compose.yml -f docker-compose.staging.yml \
      ps --status running --services 2>/dev/null | wc -l | tr -d " "
)"

current_status="healthy"
detail="ready=$ready_body health=$health_body services=$running_services"
if [ "$ready_body" != '{"ok":true}' ] ||
  [ "$health_body" != '{"ok":true,"billingEnabled":false}' ] ||
  [ "$running_services" -lt 6 ]; then
  current_status="unhealthy"
fi

previous_status="$(cat "$state_file" 2>/dev/null || true)"
printf "%s\n" "$current_status" >"$state_file"
logger -t jawnix-cutover-monitor "$current_status $detail"

if [ -n "$previous_status" ] && [ "$previous_status" != "$current_status" ]; then
  bot_token="$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' "$app_dir/.env" | head -1)"
  chat_id="$(sed -n 's/^TELEGRAM_CHAT_ID=//p' "$app_dir/.env" | head -1)"
  if [ -n "$bot_token" ] && [ -n "$chat_id" ]; then
    curl -fsS --max-time 20 \
      -X POST "https://api.telegram.org/bot${bot_token}/sendMessage" \
      --data-urlencode "chat_id=$chat_id" \
      --data-urlencode "text=Jawnix production changed from ${previous_status} to ${current_status}. ${detail}" \
      >/dev/null 2>&1 || true
  fi
fi

[ "$current_status" = "healthy" ]
