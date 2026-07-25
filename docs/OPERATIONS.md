# VPS deployment, migration, cutover, and rollback

## Protected baseline

The pre-refactor production baseline is commit `119d072bcf1a3ccc961847702e8a16ce1c109d52`, tagged and pushed as `backup/pre-vps-batch-platform-20260721T190329Z`. The linked feature worktree was at `6a338a184b555dcab97680088f3a6b59af0cfd4d`.

The verified local recovery set is outside Git at:

```text
.context/backups/20260721T190329Z/
```

It contains a verified `git bundle --all`, source archives, encrypted Railway variables, an encrypted Supabase application-data export, source checksums, and restore instructions. Its encryption password is in the macOS Keychain service `jawnix-backup-20260721T190329Z`, account `noahbleicher`. Supabase Auth was not exported or changed.

Do not stop Railway or delete old Supabase application tables during provisioning, migration, cutover, or the first 48 hours after cutover.

## VPS provisioning

1. Provision a Linux VPS with Docker Engine, the Compose plugin, SSH-key access, and a firewall allowing only TCP 22/80/443 and UDP 443.
2. Clone this branch, copy `.env.example` to `.env`, and replace every placeholder. Generate independent database, session, and Restic passwords.
3. Configure the Telegram webhook at `https://jawnix.com/api/integrations/telegram/webhook` with the bot token, random webhook secret, destination chat ID, and comma-separated authorized Telegram user IDs. Register it with Telegram's `setWebhook` API using the same `secret_token`.
4. Verify `jawnix.com` in Resend, configure `Jawnix <hai@jawnix.com>`, and set the webhook to `https://jawnix.com/api/integrations/resend/webhook` for delivered, bounced, complained, and failed events.
5. Configure a private encrypted Restic repository and credentials. Do not reuse the database or session secret as its password. The backup service creates separate database/base and WAL snapshots so a large WAL archive cannot block the daily logical dump. It creates a physical base backup on the configured UTC weekday and expires VPS material after 14 days. Install `ops/com.jawnix.external-backup.plist` on the Mac so `ops/macos-backup-pull.sh` creates the second encrypted copy under `/Volumes/Peely SSD/Jawnix Backups`.
6. Run `./scripts/render-config.sh`, then `docker compose config` and `docker compose build`.
7. Run `docker compose up -d postgres`, `docker compose run --rm migrate`, then `docker compose up -d`.
8. Confirm `/api/healthz`, `/api/readyz`, container health/logs, PostgreSQL `archive_mode`, and a Restic snapshot before importing production data. Force one initial physical base backup with `docker compose run --rm -e JAWNIX_FORCE_BASEBACKUP=true backup /app/ops/backup.sh`.

The current VPS already has an edge Caddy container for another application. Staging therefore uses `docker-compose.staging.yml`: the Jawnix Caddy listens only on the shared Docker edge network, while the existing edge Caddy terminates TLS for `staging.jawnix.com`. Do not bind a second container to host ports 80/443 or stop the existing `buzz-prod` stack.

`config.js` contains only the Supabase browser URL and publishable/anon key. Service-role, Telegram, Resend, PostgreSQL, and Restic secrets remain server-side.

The PostgreSQL initialization hook enables password-authenticated replication only for physical backups on the private Docker network. If attaching this stack to an already-initialized PostgreSQL volume, add the equivalent `host replication` rule to `pg_hba.conf` and reload PostgreSQL before forcing the first base backup.

## Staging deployment record

The VPS staging deployment is available at `https://staging.jawnix.com`.
Production `jawnix.com` DNS remains on the legacy Cloudflare/Railway path. The
VPS database is additive and does not overwrite legacy Supabase application
tables.

Current reconciled staging totals are 8,579,356 unique inventory phones,
8,870,024 provenance rows, 4,588,285 distribution events, 16 profiles, two
migration audits, and 115,875 quarantined rows. See `ACCEPTANCE.md` for the
complete evidence and remaining production gates.

## Source migration

Treat the Mac `dat` directory as read-only. Copy pinned source files to a staging volume on the VPS; never mount or run migration commands against the originals. The accepted source snapshot is:

| Source | Rows | SHA-256 |
|---|---:|---|
| `util/archive/manifest.csv` | 6,657,590 CSV-aware rows | `6ba4eecc0c067b46d8251dd40a6b74b749c63a5b21e409e2d76489d28ee11cbb` |
| `global/all_combined.csv` | 5,007,624 data rows | `e88ea28b31f758786cc8839e7653955e2e1141f19822278b24700adb031d4339` |
| `health_leads/data/leads.db` | 5,735,955 rows / 2,305,025 valid distinct phones | `00ccc0eba3362da64fb47817efeed722dde824f7d30221108fe6e1103faf7dea` |
| `redistribution/` history set | 177 files | `24e4f385ad23f91b8d4f361c56802f60ea7b63590181ac2eec09dc9077e2c72d` |

Create the corrected staging copy, then run imports in this order:

```sh
docker compose run --rm -v /srv/jawnix/migration:/migration api python -m jawnix_data prepare-config \
  /migration/config.source.json /migration/config.json \
  --overrides /app/config/migration-overrides.json
docker compose run --rm -v /srv/jawnix/migration:/migration:ro api python -m jawnix_data import-config /migration/config.json
docker compose run --rm -v /srv/jawnix/migration:/migration:ro api python -m jawnix_data import-manifest \
  /migration/manifest.csv \
  --expected-sha256 6ba4eecc0c067b46d8251dd40a6b74b749c63a5b21e409e2d76489d28ee11cbb
docker compose run --rm -v /srv/jawnix/migration:/migration:ro api python -m jawnix_data import-history /migration/redistribution
docker compose run --rm -v /srv/jawnix/migration:/migration:ro api python -m jawnix_data import-scraper-db \
  /migration/leads.db \
  --expected-sha256 00ccc0eba3362da64fb47817efeed722dde824f7d30221108fe6e1103faf7dea
docker compose run --rm -v /srv/jawnix/migration:/migration:ro api python -m jawnix_data import-supabase /migration/supabase-export
docker compose run --rm api python -m jawnix_data provision-customer-mappings \
  /app/config/customer-agent-mappings.csv --invite-missing
```

The config import intentionally blocks on invalid configured states. The committed overrides apply the explicit decisions `IO → IA` and `CN → CT` only in the staging copy, then add Matthew and Ali to Summit. The source file remains read-only.

Manifest data wins on phone collisions. Scraper-only rows use their valid source state, then phone-derived state; titles fall back through company, full name, and niche. Malformed records enter `quarantined_rows`. Migration checksums and counts enter `migration_audits`, so identical reruns are skipped.

After import, reconcile table totals, inventory by state, distinct normalized phones, agent and agency histories, quarantine reasons, source provenance, profiles, and pending requests. Run `python -m jawnix_data inventory` and state-specific dry runs before enabling customers.

The accepted staging import produced 6,564,999 valid manifest inventory rows
and 92,591 manifest quarantines. Scraper deduplication produced 2,305,025
valid distinct phones: 290,668 manifest overlaps and 2,014,357 new inventory
phones. Eighty-eight history files had an unambiguous dated recipient and were
imported; 89 ambiguous files were skipped rather than guessed.

## Customer mapping and acceptance

Sign in with an existing Supabase admin, open Recipients, and synchronize Supabase users. Every proposed customer-to-agent mapping starts unconfirmed. Manually select and confirm each agent; requests remain blocked until confirmation.

Before cutover, prove:

- invited-user Supabase login and VPS session exchange against the clean Auth project;
- profile state save and all/subset request validation;
- 1 and 100,000-row boundaries;
- Telegram Approve/Reject, webhook-secret and authorized-user checks, replay behavior, and duplicate clicks;
- exact CSV row count, unique phones, state scope, `phone,title` header, and recorded checksum;
- shortage creates zero distribution events and Retry works after inventory arrives;
- generation failure commits no allocation and delivery failure reuses its artifact;
- Resend delivery, bounce, complaint, and failed webhooks update state and Telegram;
- two concurrent allocations have no overlapping phone IDs;
- staging restore from Restic and `git bundle verify` both pass.

For the 10-million-row performance gate, run the guarded harness against a disposable staging database. Acceptance is allocation plus CSV generation in under five minutes without API health failures. Never run it in production.

```sh
JAWNIX_ALLOW_SYNTHETIC_LOAD_TEST=YES \
JAWNIX_LOAD_TEST_HEALTH_URL=https://staging.jawnix.com/api/readyz \
python scripts/postgres_load_test.py \
  --inventory-rows 10000000 --request-rows 100000 --max-seconds 300
```

## Backup verification and external copy

List and check the VPS repository from the backup container:

```sh
docker compose exec backup restic snapshots
docker compose exec backup restic check
```

Run the installed Mac pull whenever Peely SSD is mounted:

```sh
"$HOME/Library/Application Support/Jawnix/macos-backup-pull.sh"
```

The pull uses a dedicated read-only SSH key, copies snapshots into an
independently encrypted Restic repository, applies the same 14-day retention,
and runs `restic check`. Restic copy is resumable and deduplicated; an
interrupted run can be rerun safely. Verify that the database and WAL snapshot
IDs appear in the external repository before cutover.

The launch agent runs daily at 01:00 local time but only while the Mac and drive
are available. A missed run does not affect the VPS repository.

## Cutover

1. Keep Railway live while the VPS is populated and tested.
2. Rotate all credentials disclosed during staging and update only the VPS and required provider configurations. Recheck health before continuing.
3. Register and test the Resend delivery/failure webhook, confirm the collector command if applicable, activate customer logins, and complete the Peely SSD snapshot copy.
4. Lower DNS TTL at least one previous TTL window before cutover.
5. Announce a short request/redistribution pause. Disable only new requests and local redistribution; leave login and history available.
6. Export/import the final Supabase and source-data delta, reconcile counts, and verify every active customer mapping.
7. Take and verify a VPS database backup and Restic snapshot.
8. Change the `jawnix.com` A/AAAA records to the VPS only after explicit approval. Confirm Caddy certificate issuance and the complete login → request → Telegram approval → CSV email → delivered flow.
9. Re-enable requests and monitor API errors, worker failures, queued/running jobs, PostgreSQL locks/storage, Telegram notifications, Resend bounces/complaints, backups, and DNS from multiple resolvers for 48 hours.
10. After 48 clean hours, Railway may be scaled down only after explicit approval. Do not delete Railway, Supabase tables, the rollback tag, or the recovery bundle as part of this change.

## Rollback

1. Pause new VPS requests and worker processing.
2. Restore the prior `jawnix.com` DNS records for Railway.
3. If Railway configuration changed, decrypt and restore `railway-variables.json.enc` from the protected recovery set.
4. Redeploy `backup/pre-vps-batch-platform-20260721T190329Z` (or restore from the verified bundle/archive).
5. Verify login, admin, requests, and legacy Supabase data on Railway.
6. Preserve the VPS database and logs for reconciliation; do not merge them back into Supabase automatically.

The VPS migration is additive. Rollback does not overwrite or delete old Supabase application data.
