#!/bin/zsh
set -eu

source_dir="${0:A:h}"
support_dir="$HOME/Library/Application Support/Jawnix"
log_dir="$HOME/Library/Logs/Jawnix"
agent_path="$HOME/Library/LaunchAgents/com.jawnix.external-backup.plist"
script_path="$support_dir/macos-backup-pull.sh"

mkdir -p "$support_dir" "$log_dir" "$HOME/Library/LaunchAgents"
install -m 700 "$source_dir/macos-backup-pull.sh" "$script_path"
sed \
  -e "s|__JAWNIX_BACKUP_SCRIPT__|$script_path|g" \
  -e "s|__JAWNIX_LOG_DIR__|$log_dir|g" \
  "$source_dir/com.jawnix.external-backup.plist" >"$agent_path"
plutil -lint "$agent_path"
launchctl bootout "gui/$UID/com.jawnix.external-backup" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$agent_path"
launchctl enable "gui/$UID/com.jawnix.external-backup"
print -- "Installed $agent_path"
