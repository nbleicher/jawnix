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
2. Clone this branch, copy `.env.example` to `.env`, and replace every placeholder. Generate independent database, session, and Restic passwords. On the shared production VPS only, also uncomment the `COMPOSE_FILE` line so compose can never omit the edge adapter (see below).
3. Configure the Telegram webhook at `https://jawnix.com/api/integrations/telegram/webhook` with the bot token, random webhook secret, destination chat ID, and comma-separated authorized Telegram user IDs. Register it with Telegram's `setWebhook` API using the same `secret_token`.
4. Verify `jawnix.com` in Resend, configure `Jawnix <hai@jawnix.com>`, and set the webhook to `https://jawnix.com/api/integrations/resend/webhook` for delivered, bounced, complained, and failed events.
5. Configure a private encrypted Restic repository and credentials. Do not reuse the database or session secret as its password. The backup service creates separate database/base and WAL snapshots so a large WAL archive cannot block the daily logical dump. It creates a physical base backup on the configured UTC weekday and expires VPS material after 14 days. Install `ops/com.jawnix.external-backup.plist` on the Mac so `ops/macos-backup-pull.sh` creates the second encrypted copy under `/Volumes/Peely SSD/Jawnix Backups`.
6. Run `docker compose config` and `docker compose build`. (`/config.js` is rendered by Caddy from the environment — there is no file to generate.)
7. Run `docker compose up -d postgres`, `docker compose run --rm migrate`, then `docker compose up -d`.
8. Confirm `/api/healthz`, `/api/readyz`, container health/logs, PostgreSQL `archive_mode`, and a Restic snapshot before importing production data. Force one initial physical base backup with `docker compose run --rm -e JAWNIX_FORCE_BASEBACKUP=true backup /app/ops/backup.sh`.

`docker-compose.edge.yml` is the shared edge's adapter, and it is **required in
production**. buzz-prod still shares the VPS: `buzz-prod-caddy-1` owns
`0.0.0.0:80` and `:443` on this box and routes `jawnix.com` to
`reverse_proxy jawnix-caddy:8080`. The adapter names the container `jawnix-caddy`,
serves plain HTTP on `:8080`/`:8081`, releases 80/443, and joins
`buzz-prod_buzz-net` so the edge can reach it.

An earlier version of this paragraph said buzz-prod was gone and the override had
been deleted on 2026-07-28. **That was wrong**, and acting on it took jawnix.com
down for ~25 minutes on 2026-07-30 the moment containers were recreated from a
compose file that no longer produced `jawnix-caddy`. The file was restored and
renamed from `docker-compose.staging.yml`, because calling a production
requirement "staging" is what made it look disposable.

Production sets `COMPOSE_FILE=docker-compose.yml:docker-compose.edge.yml`, so a
bare `docker compose` command loads both. Never run compose here without the
adapter.

`staging.jawnix.com` resolves to the same box and is served by the same stack
behind the same edge — buzz-prod's Caddy routes it to `jawnix-caddy:8080`
alongside production. A separate staging host (one without the shared edge)
runs the base stack directly, owning ports 80/443 and terminating its own TLS,
with `COMPOSE_FILE` left commented in `.env`:

```sh
JAWNIX_DOMAIN=staging.jawnix.com docker compose up -d
```

Set `JAWNIX_SCRAPER_OPS_DOMAIN` to the staging Scraper hostname in the same way. Nothing else differs from production.

`/config.js` is rendered by Caddy from the environment and contains only
browser-public values: the Supabase URL and publishable/anon key, the Scraper
Operations origin, and the billing flag. Service-role, Telegram, Resend,
PostgreSQL, and Restic secrets remain server-side.

## Deploying the application host

`ops/deploy.sh` is the only sanctioned path for updating the application tree
at `/srv/jawnix/app`. Do not copy a hand-picked file set or run `scp` or a
separate `rsync` command against that directory. The host has no Git checkout,
so every deployment originates from a clean local checkout of a tagged commit
on `origin/main`.

Create and push the release tag from an up-to-date `main`, then check out that
exact tag. Run the deploy script interactively with the SSH destination in
`JAWNIX_DEPLOY_TARGET`:

```sh
git fetch origin main --tags
git switch --detach production-YYYYMMDDTHHMMSSZ
JAWNIX_DEPLOY_TARGET=root@159.195.15.51 \
  ops/deploy.sh production-YYYYMMDDTHHMMSSZ
```

The script refuses a dirty checkout, a tag that is absent from `origin`, a
checkout that does not exactly match the requested tag, or a tagged commit that
is not on the freshly fetched `origin/main`. It builds the rsync source from the
tagged commit rather than from ignored local files. It always prints an
itemized `rsync --dry-run` first and requires the exact confirmation shown in
the prompt before repeating the same sync without `--dry-run`.

The sync uses `--delete`, but its pinned excludes protect the production
`.env`, `config.js`, `batches/`, `backups/`, `invoices/`, `monitoring/`,
`restic-repository/`, `migration/`, and `user-account-migration/`. These paths
contain host-owned configuration, datasets, generated invoices, monitoring
state, backup material, or migration evidence and must never be removed or
replaced by an application deploy. The backup, monitoring, and migration
directories are normally siblings of `/srv/jawnix/app`; keeping them excluded
also protects legacy or accidentally nested copies.

`config.js` is gitignored and therefore absent from the deploy source, so
`--delete` would remove the host's copy. Caddy has rendered `/config.js` from
the environment since #103 and the on-disk file is no longer read, but it
holds real Supabase credentials and is host-owned, so the deploy leaves it
alone rather than destroying it as a side effect.

The excludes also cover build artifacts that exist only on the host —
`.venv/`, `jawnix_vps.egg-info/`, and a stray `jawnix-dev.db`. Nothing
references them (the application runs from the Compose images), and syncing
them adds thousands of lines of irrelevant churn to the dry-run diff that an
operator must review, which is how a genuinely dangerous deletion gets missed.

After the source sync, connect to the host, confirm that `.env` still sets
`COMPOSE_FILE=docker-compose.yml:docker-compose.edge.yml`, and use the normal
Compose sequence for the release: build, run migrations, recreate the intended
services, and verify `/api/healthz`, `/api/readyz`, container health/logs, and a
current Restic snapshot.

## Administrator MFA break-glass recovery

Break-glass Recovery is for an administrator who has lost both the Primary and
Backup Authenticators. It is not a password reset, cannot be initiated from the
application, and restores access only to MFA enrollment. Every administrator
endpoint remains closed until two replacement Authenticator Factors are
verified and the new session reaches AAL2.

Prerequisites:

1. The operator verifies the recipient's identity out of band using the
   business's incident procedure.
2. A second person explicitly authorizes the recovery. The operator and
   authorizer must be different named people.
3. Open an incident or support ticket containing the reason, identity evidence,
   authorizer approval, and intended execution time.
4. Confirm a current database backup and access to the Supabase service-role
   credential. Never place that credential in the command line, ticket, or
   application configuration.

From the deployed revision, run the command in a one-off API container. Replace
every example value and copy the immutable Supabase Auth user UUID from the
verified operator record:

```sh
docker compose run --rm api python -m jawnix_data admin-mfa-break-glass \
  --target-user-id 00000000-0000-4000-8000-000000000000 \
  --target-email administrator@example.com \
  --operator "Named operator" \
  --authorizer "Different named authorizer" \
  --reason "Both enrolled authenticators were lost" \
  --reference "INC-0000" \
  --confirm REVOKE-AND-REENROLL
```

The command first increments the administrator's Jawnix session generation and
commits an authorization Audit Entry. It then confirms the provider identity is
still an administrator with the exact supplied email, removes every provider
factor, and commits the completed Audit Entry. If provider work fails, stop:
sessions remain revoked and the authorized-attempt entry remains durable.
Record the error in the incident and investigate before rerunning.

After success, have the recipient sign in with the existing password and enroll
a new Primary Authenticator and a separately stored Backup Authenticator.
Confirm that an administrative route refuses the enrollment-only session, both
factors show their expected last-use information, and the completed Audit Entry
names the recipient, operator, authorizer, reference, reason, removed-factor
count, and `mfa_enrollment_only` access result. Close the incident only after
those checks pass.

## Private Scraper Operations mount

Jawnix links the Scraper control plane from Admin at `/admin/scraper/`. After
verifying the Jawnix administrator session, the API performs a short-lived
handoff to the dedicated `scraper.jawnix.com` browser origin. That origin
exposes only `/admin/scraper/*`, uses a separate host-only session and
proxy-scoped CSRF token, rewrites dashboard and HTMX paths, and injects the
Scraper Basic Auth credential server-side. This origin separation prevents
Scraper HTML or JavaScript from accessing native Jawnix sessions and APIs. Do
not expose either credential in `config.js` or any browser-facing environment
value.

Connect the two VPS hosts with WireGuard before enabling the mount:

Use the verified Scale release
`scraper-source-baseline-2026-07-27-v4`. Do not deploy it until the current
production image and source revision have been recorded for rollback as required
by Jawnix issue #29.

This pin remains the deployment source only until the Scraper ownership cutover
below passes. At that point it becomes a rollback reference for seven days;
Jawnix's tagged `main` revision becomes the production source for
`scraper-control`, the worker, migrations, and schedules. Do not delete the
Scale tag or stopped stack while it is the rollback path.

1. Assign a private WireGuard address to each host (the examples use Jawnix
   `10.77.0.1` and Scraper `10.77.0.2`) and configure each as the other's only
   peer.
2. On the Scraper host, set `DASHBOARD_BIND_ADDRESS=10.77.0.2` in the Scale
   environment and recreate only the dashboard container. Keep port 8090
   closed on the public interface.
3. Permit TCP 8090 on the Scraper host only on `wg0` and only from the Jawnix
   WireGuard address. Reject the same port on the public interface.
4. Point `scraper.jawnix.com` at the Jawnix edge, set
   `JAWNIX_SCRAPER_OPS_ORIGIN=https://scraper.jawnix.com` and
   `JAWNIX_SCRAPER_OPS_DOMAIN=scraper.jawnix.com`. In production the shared
   edge routes `scraper.jawnix.com` to `jawnix-caddy:8081`; on a separate
   staging host, set the same two variables to the staging Scraper hostname and
   the base Caddy terminates its TLS directly.
5. On Jawnix, set `JAWNIX_SCRAPER_OPS_URL=http://10.77.0.2:8090` plus
   `JAWNIX_SCRAPER_OPS_USER` and `JAWNIX_SCRAPER_OPS_PASSWORD`, then recreate
   the API and Caddy services.
6. From inside the Jawnix API container, verify that the Scraper health route
   works through WireGuard and is unreachable through the Scraper's public IP.
   Then sign in as a Jawnix administrator and open `/admin/scraper/`.

If the peer or upstream is unavailable, the mounted route returns a closed
unavailable page with its process's last successful connection and a Retry
link. Native Jawnix administration, feedback, and analytics routes do not
depend on the Scraper connection.

The PostgreSQL initialization hook enables password-authenticated replication only for physical backups on the private Docker network. If attaching this stack to an already-initialized PostgreSQL volume, add the equivalent `host replication` rule to `pg_hba.conf` and reload PostgreSQL before forcing the first base backup.

## Scraper ownership cutover

Do not schedule this window until a Stage C rehearsal report says **GO**. The
[2026-08-02 report](rehearsals/2026-08-02-scraper-cutover.md) is a standing
**GO**: all three blockers are cleared against the real acquisition store,
with an off-host dump that reconciles exactly, a 4,603,285-row history import
that reruns idempotently, and Jawnix-owned rollover proven with no model
credential on the control service. It supersedes the
[2026-07-31 NO-GO](rehearsals/2026-07-31-scraper-cutover.md) and the
[retracted 2026-08-01 GO](rehearsals/2026-08-01-scraper-cutover.md).

### The acquisition store

The live store is a **loopback-bound Docker PostgreSQL 16.14 on the Scale
control VPS itself** (`51.81.184.162`, WireGuard `10.77.0.2`), container
`gms-scale-db-1`, `postgresql://postgres@127.0.0.1:5432/gmaps_pro`. It
publishes no port beyond loopback, so it is invisible from the application
host and the worker box.

Access is `ubuntu@51.81.184.162` with passwordless sudo — the OVH Ubuntu image
disables root login, so `root@` attempts always fail regardless of the
password. The operator key is `~/.ssh/scale_vps`.

The database on the application host that a worker `.env` names as `gmaps_pro`
is a stale partial copy (17,650 businesses, all with an empty `state`). **Do
not treat a DSN found in any `.env` as authoritative.** Cross-check a
candidate database's row counts and per-state distribution against the live
dashboard's `/api/dashboard` and `/database` before backing anything up; a
2026-08-01 rehearsal backed up the wrong database and had to retract a GO.

**The pipeline is live.** Eight worker containers plus `gms-serve`,
`gms-enqueue`, and nine timers run continuously on the control VPS; the window
must stop them deliberately. There are **no pending additive migrations** —
the store's `scale_migrations` ledger already matches this repository through
`20260727000000-dataset-publications.sql`.

### Required artifacts and rehearsal isolation

Before the maintenance window, produce and retain all of these as one change
record:

- a custom-format dump of acquisition database `gmaps_pro`, its SHA-256, a
  successful `pg_restore --list`, and a successful restore into a disposable
  PostgreSQL instance;
- the immutable SQLite keyword-history import artifact containing
  `enqueue_log`, `keyword_history`, and `businesses`, plus its SHA-256 and the
  row count of each table;
- the latest Jawnix custom-format dump and Scraper Dataset snapshot, with their
  existing Restic snapshot IDs and checksums;
- the current legacy Scale image, source revision, service/timer state, and
  configuration digest; and
- the tagged Jawnix `main` revision and image digests that will be deployed.

Until acquisition backup automation is installed, create the acquisition dump
on its host and copy it into encrypted storage before stopping anything:

```sh
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="/root/acquisition-gmaps_pro-$timestamp.dump"
docker exec scraper-db-1 pg_dump \
  --username postgres --dbname gmaps_pro \
  --format=custom --no-owner --no-acl >"$dump"
sha256sum "$dump"
docker exec -i scraper-db-1 pg_restore --list <"$dump" >/dev/null
```

This host-local file is not a backup until it has been copied off the
acquisition host into encrypted storage and the copy's digest matches. Record
the storage snapshot or object identifier. Do not proceed when the dump, copy,
or restore verification is missing.

The rehearsal must be incapable of resolving production database names or
addresses. Use unique Compose project and volume names, keep PostgreSQL
unpublished or bind it only to loopback, and inspect every proposed host port
before startup:

```sh
lsof -nP -iTCP -sTCP:LISTEN
docker compose -p scraper-cutover-rehearsal \
  -f scraper/docker-compose.box.yml config
```

Existing SSH tunnels can occupy otherwise plausible rehearsal ports. Exercise
service calls by Docker-internal service name on the rehearsal network, not by
a host port. Confirm container project labels, mounts, networks, and database
DSNs before the first request. Stop immediately if any value names a production
host.

Restore the acquisition dump into a disposable `gmaps_pro`, then run the
migrations from the tagged cutover revision and verify both migration ledgers:

```sh
docker compose -p scraper-cutover-rehearsal \
  -f scraper/docker-compose.box.yml up -d db
docker compose -p scraper-cutover-rehearsal \
  -f scraper/docker-compose.box.yml exec -T db \
  pg_restore --username postgres --dbname gmaps_pro \
  --clean --if-exists --no-owner --no-acl <acquisition-gmaps_pro.dump
docker compose -p scraper-cutover-rehearsal \
  -f scraper/docker-compose.box.yml run --rm migrate
docker compose -p scraper-cutover-rehearsal \
  -f scraper/docker-compose.box.yml exec -T db \
  psql --username postgres --dbname gmaps_pro \
  --command='TABLE scraper_migrations' \
  --command='TABLE scale_migrations'
```

Verify the immutable keyword-history source before importing it into the
restored Jawnix database:

```sh
history=/restore/acquisition-history.db
sha256sum "$history"
sqlite3 -readonly "$history" \
  "SELECT 'enqueue_log', count(*) FROM enqueue_log
   UNION ALL SELECT 'keyword_history', count(*) FROM keyword_history
   UNION ALL SELECT 'businesses', count(*) FROM businesses;"
docker compose run --rm -v /restore:/restore:ro api \
  python -m jawnix_data import-keyword-history "$history" \
  --expected-sha256 REPLACE_WITH_VERIFIED_SHA256
```

Require the command's `sourceRowsByTable` to equal the three source counts and
its `checksum` to equal the verified file digest. Rerun it and require
`skipped: true`, `inserted: 0`, and `updated: 0` before accepting idempotency.

### Maintenance-window sequence

1. Announce the acquisition maintenance window. Pause new acquisition work in
   Jawnix, then stop the legacy Scale workers and timers; keep the legacy
   database and dashboard available for rollback.
2. Create and verify fresh acquisition, Jawnix, and Scraper Dataset backups as
   described above. Record the legacy image/source and every stopped unit.
3. Deploy the tagged Jawnix source to both hosts without starting the new
   worker, timers, or public control path. Build the vendored worker image and
   record its digest.
4. Apply only the additive acquisition and Jawnix migrations. Verify every
   migration ledger and reconcile the pre-migration row counts.
5. Run the full keyword-history import with its verified checksum. Reconcile
   all three source counts, the normalized union count, persisted import count,
   and idempotent rerun.
6. Start `scraper-control` on the acquisition host's WireGuard address. Run the
   typed contract suite and authenticated health, reads, exports, monitoring,
   saves, enqueue trigger, pipeline, runtime, rollover, and schedule checks
   against the restored/current data.
7. Install `OPENROUTER_API_KEY` only on the Jawnix application host. Automatic
   rollover must enable and disable successfully without that secret on the
   acquisition host; failure is an immediate rollback condition.
8. From the tagged Jawnix source revision, build the application services, run
   `docker compose run --rm migrate`, and recreate `api`, `worker`, `scheduler`,
   `backup`, and `caddy` with `docker compose up -d`. Point Jawnix at the typed
   control endpoint and exercise generation, reads, CSV exports, monitoring,
   keyword preview/save/enqueue, pipeline pause/resume, runtime preview/save,
   rollover, and scheduled controls. Verify audit entries and accepted
   generation-draft state.
9. Start the new workers and timers, verify heartbeats and queue movement, then
   disable the legacy Scale dashboard. Record timings and outputs for every
   step.
10. Once the complete smoke passes, retire
    `scraper-source-baseline-2026-07-27-v4` as the production deployment source.
    Keep its stopped stack, image, tag, configuration, and backups intact for
    the seven-day rollback window. Jawnix's tagged `main` revision is now the
    sole deployment source.

### Seven-day Scraper rollback

1. Pause acquisition in Jawnix and stop the new workers, timers, and
   `scraper-control`.
2. Restore the prior tagged Jawnix image and its legacy Scraper endpoint
   settings.
3. Restart the recorded Scale dashboard, workers, and timers against the
   existing acquisition database. Do not downgrade additive migrations.
4. Confirm legacy `/healthz`, Dashboard, Database, History, exports, queue
   movement, and worker heartbeats; then re-enable acquisition.
5. Preserve the failed-cutover databases, logs, import report, and image
   digests for reconciliation. Do not delete or automatically merge data.

## Production deployment record

Production DNS cut over to the VPS on 2026-07-25 at approximately 03:33 UTC.
`jawnix.com`, `www.jawnix.com`, and `staging.jawnix.com` resolve to
`159.195.15.51`; production and staging are served by the same application
stack. Railway and the legacy Supabase project remain live for rollback through
the observation window.

Current reconciled production totals are 9,244,326 unique inventory phones,
9,541,530 provenance rows, 4,588,286 distribution events, 16 profiles, one
delivered production smoke request, three migration audits, and 143,037
quarantined rows. See `ACCEPTANCE.md` for the complete evidence.

The 16 profiles are customer recipients. The separate Supabase Auth identity
`noah@jawnix.com` is the administrator and intentionally has no customer
profile or agent mapping. `noah@urpriorityhealth.com` remains the customer
mapped to the `noah` agent in Summit.

Admin deletion of an agent or agency is intentionally reversible at the
database level. It sets `deleted_at`, disables the affected records, removes
them from active hierarchy and mapping choices, and unassigns customers. It
does not delete lead requests or distribution events. Deleting an agency
applies the same tombstone to every member agent. Restore only with an audited
database update that clears `deleted_at`; customer mappings must still be
reviewed and reconfirmed.

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

## One-time User Account migration

This is an offline cutover operation, not an administrator screen. Its input is
a reviewed UTF-8 CSV with exactly one row for every active, non-deleted durable
Customer:

```csv
customer,email,agency
noah,noah.new@example.com,summit
```

`customer` and `agency` accept an exact database ID, slug, or name.
`independent` is the explicit no-Agency destination. Prefer immutable slugs in
the approved file; duplicate names are reported as ambiguous and block apply.
The destination email must be fresh: an existing provider identity, local User
Account, or pending invitation is a conflict.

### 1. Rehearse and approve the dry-run

Restore a current production dump into an isolated copy and point the command at
a non-production Supabase project that cannot email Customers. Run the schema
upgrade, then:

```sh
docker compose run --rm \
  -v /srv/jawnix/user-account-migration:/migration:ro \
  api python -m jawnix_data user-account-migration-dry-run \
  /migration/user-account-mappings.csv \
  > /srv/jawnix/user-account-migration/dry-run.json
```

Dry-run performs database reads and provider identity listing only. It never
flushes or commits, never invites an identity, and reports
`"mutationPerformed": false`. Review every row and require
`summary.valid == true`. The report includes missing and ambiguous Customers,
coverage gaps, duplicate emails, account and invitation conflicts, Agency
status, membership impact, and permanent-history counts. Record the exact
`planChecksum`; apply refuses if the current preflight no longer matches it.

In the isolated copy, run apply with a rehearsal backup receipt, accept sample
invitations, and verify the old account changes only at acceptance. Confirm
Customer IDs and Distribution Event IDs and snapshots are unchanged. Complete
the rollback rehearsal by discarding the mutated copy, restoring the backup
again, and confirming that it contains no
`user_account_migration_runs`, mappings, artifacts, or new User Accounts.
Record this rehearsal in the production backup receipt.

### 2. Create and verify the production backup gate

Pause Customer and administrative writes. Create a fresh custom-format
PostgreSQL dump, store it in the encrypted Restic repository, run
`restic check`, restore the dump into an isolated database, and run the
readiness and reconciliation checks against that restore. Compute the dump's
SHA-256. Only then create a root-readable receipt outside the repository:

```json
{
  "databaseSnapshot": "restic:production-YYYYMMDDTHHMMSSZ",
  "databaseDumpSha256": "64-lowercase-hex-characters",
  "resticCheckCompletedAt": "2026-07-29T12:00:00Z",
  "restoreRehearsalReference": "CHANGE-OR-INCIDENT-REFERENCE",
  "verifiedAt": "2026-07-29T12:30:00Z",
  "verifiedBy": "Named verifier"
}
```

Apply refuses malformed receipts, blank evidence, invalid dump digests, naive
timestamps, future timestamps, or verification/Restic checks older than 24
hours. A resume may use the original receipt while it remains current. If work
crosses the 24-hour boundary, take and verify a new backup of the partially
migrated state and pass its new receipt; the journal appends that evidence and
the final artifact retains every receipt. Never edit an existing receipt to
make it look current. A receipt is an attestation of completed backup and
restore work; creating it before those checks defeats the gate and is
prohibited.

### 3. Apply, resume, and reconcile

From the same deployed revision used for dry-run:

```sh
plan_sha256="$(jq -r .planChecksum \
  /srv/jawnix/user-account-migration/dry-run.json)"

docker compose run --rm \
  -v /srv/jawnix/user-account-migration:/migration \
  api python -m jawnix_data user-account-migration-apply \
  /migration/user-account-mappings.csv \
  --approved-plan-sha256 "$plan_sha256" \
  --verified-backup /migration/verified-backup.json \
  --artifact-dir /migration/reconciliation \
  --operator "Named migration operator" \
  --reason "Approved one-time User Account cutover" \
  --confirm APPLY-USER-ACCOUNT-MIGRATION
```

The command commits an interruption journal before each provider call. Each
provider identity receives the migration run and mapping IDs as metadata. If
the provider accepts an invitation and the command times out, rerun the exact
command: it recovers that identity by metadata instead of sending another
invitation. Never edit the CSV, plan hash, or backup receipt while resuming.
If a fresh backup becomes necessary, create a new receipt file and change only
the `--verified-backup` path.

Existing User Accounts remain active while replacement invitations are
pending. Invitation acceptance performs the atomic swap through the normal
User Account seam. Apply may update current Agency membership through the
normal confirmed assignment seam; it preserves Customer IDs, Distribution
Event IDs and snapshots, and permanent no-repeat history.

Successful apply writes one read-only,
`user-account-migration-<run>-<sha256>.json` reconciliation artifact and stores
the same content and digest in `user_account_migration_artifacts`. Verify the
file's `artifactSha256` against its canonical `artifact` value and retain it
with the change record. It records every mapping, invitation state,
deactivation result or deferral, Agency result, history counts, backup receipt
digest, and identifier-preservation assertion. A completed rerun verifies and
returns the same artifact rather than creating a second migration.

After reconciliation, resume writes and monitor invitation delivery and
acceptance. An interrupted apply is normally recovered by rerun, not database
rollback. If a full rollback is authorized, first preserve the migration
journal, artifact, provider identity list, and logs; restore the verified
database; then reconcile and revoke only provider identities tagged with the
recorded migration run ID. Do not guess from email addresses, and do not
deactivate former accounts before the restored database and provider state
agree.

The accepted staging import produced 6,564,999 valid manifest inventory rows
and 92,591 manifest quarantines. Scraper deduplication produced 2,305,025
valid distinct phones: 290,668 manifest overlaps and 2,014,357 new inventory
phones. Eighty-eight history files had an unambiguous dated recipient and were
imported; 89 ambiguous files were skipped rather than guessed.

Historical note: the first production scheduler cycle downloaded the July 2026 NPPES V2 archive
from the CMS index. It atomically refreshed 7,369,238 SQLite rows before
merging them into PostgreSQL, added 664,970 new inventory phones, and
quarantined 27,162 unusable rows. The collector leaves the previous SQLite
database untouched unless download, parsing, and validation all succeed.

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

For a Scraper Dataset restore, restore the dataset directory (including
`dataset-metadata.json` and `.versions`) and validate it against the restored
PostgreSQL database before replacing active data:

```sh
python -m jawnix_data restore-scraper-dataset \
  --dataset /restore/health_leads/data/leads.db \
  --metadata /restore/health_leads/data/dataset-metadata.json
```

An older dataset or a same-version checksum mismatch is rejected. A newer
validated dataset can be replayed forward idempotently with `--apply`; this
archives the exact restored bytes, records the publication and Inventory Sync
attempt, and synchronizes PostgreSQL without exposing partial inventory.

Legacy native datasets created before versioned publication metadata are
backed up with the `legacy-metadata-missing` Restic tag and an independent
`scraper-dataset.sha256` snapshot. Restore those bytes only through the
documented legacy import flow after verifying the saved SHA-256; never invent
version or configuration metadata for them.

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
interrupted run can be rerun safely. Restic assigns new snapshot IDs in the
destination repository; verify corresponding timestamps, tags, paths, and
sizes rather than expecting the VPS snapshot IDs to remain unchanged.

The launch agent runs daily at 01:00 local time but only while the Mac and drive
are available. A missed run does not affect the VPS repository.

## Cutover monitoring

The installed `jawnix-cutover-monitor.timer` runs every five minutes. It checks
production readiness, billing-disable health, and that all six Compose services
are running. Results are written to journald under
`jawnix-cutover-monitor`; state changes generate Telegram alerts.

```sh
systemctl status jawnix-cutover-monitor.timer
journalctl -t jawnix-cutover-monitor --since "1 hour ago"
```

Keep this timer active after the 48-hour window as basic availability
monitoring. It supplements rather than replaces review of failed jobs, email
events, database storage, backup results, and application logs.

## Cutover

The initial production cutover completed on 2026-07-25. Preserve this checklist
for future migrations or a repeated cutover after rollback.

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

## UI cutover (#71)

> **Historical.** The `JAWNIX_ENABLE_NEW_UI` flag described below was retired
> after P8: the React shell is now the only UI and there is no flag to flip.
> This section is kept as the record of how the 2026-07-31 cutover was performed.

Activates the redesigned React shell (`/app`) as the production UI and retires
the legacy static pages. This is a flag flip, not a data migration: the #61
User Account migration is not run (see #70/#71 — ~10 people are invited
manually through the administrator screens), so the only irreversible-feeling
step here is customer impressions, not data.

Preflight:

1. Verify the latest database backup and Restic snapshot per "Backup
   verification and external copy". The flag change needs no data way back,
   but do not cut over without a verified backup regardless.
2. Confirm both redirect URLs are allow-listed in Supabase Auth:
   `$JAWNIX_PUBLIC_BASE_URL/portal-accept.html` and
   `$JAWNIX_PUBLIC_BASE_URL/app/accept-invitation`. Keep both until
   retirement; this is what preserves rollback for outstanding invitations.
3. Confirm `jawnix-cutover-monitor.timer` is active.

Cutover:

1. Set `JAWNIX_ENABLE_NEW_UI=true` in `/srv/jawnix/.env`.
2. `docker compose up -d api caddy` — both containers must be recreated: api
   serves the shell from the flag, and Caddy substitutes it at parse time to
   enable the legacy-URL redirects (`/`, `/login[.html]`, `/portal[.html]`,
   `/portal-accept.html`, `/admin.html` → `/app/...`, all 302).
3. Smoke-verify in production: sign-in at `/app/sign-in`; each legacy URL
   redirects; Customer Overview, Batch Request submission, Telegram approval,
   delivery email, Customer Feedback; administrator MFA challenge and each
   admin destination; Scraper step-up and workspace against the live private
   service; `/app` hard-refresh on a deep route.
4. Monitor per the cutover-monitoring section for 48 hours.

Rollback — the flag-flip rollback below applied **only until static-page
retirement (P8, 2026-08-03)**. It is no longer available: the static pages are
deleted and the Caddy redirects are unconditional, so setting
`JAWNIX_ENABLE_NEW_UI=false` now takes the site down (every legacy URL still
302s to `/app`, which the api would stop serving). UI rollback after
retirement is a redeploy of the previous release tag through `ops/deploy.sh`,
not a flag flip.

The historical flag-flip rollback was:

1. Set `JAWNIX_ENABLE_NEW_UI=false` in `/srv/jawnix/.env`.
2. `docker compose up -d api caddy`. The static pages answered again at their
   original URLs; `/app` returned 404. All redirects were 302, so no browser
   cached the cutover.

## Static-page retirement (P8, completed 2026-08-03)

Done after #71 stabilization was declared and retirement was explicitly
approved. Deleted `index.html`, `login.html`, `portal.html`,
`portal-accept.html`, `admin.html`, and `theme.css` from the image; removed
their `/srv/static` bind mounts from `docker-compose.yml`; and rewrote the
Caddyfile so the legacy-URL redirects are unconditional (no longer gated on
`JAWNIX_ENABLE_NEW_UI`) with a catch-all sending every other non-shell path to
`/app`. `/config.js` is untouched — it is rendered by Caddy from the
environment and the React shell still reads `window.JAWNIX_CONFIG`.

Still pending, and deliberately **not** part of that change: remove
`$JAWNIX_PUBLIC_BASE_URL/portal-accept.html` from the Supabase Auth redirect
allow-list. New invitations always redirect to `/app/accept-invitation`
(`_customer_invitation_redirect`), but outstanding unaccepted invitations still
carry `/portal-accept.html` links, and Supabase validates `redirect_to` against
the allow-list **before** the Caddy 302 can run. Remove that entry only once
every invitation issued before
cutover has been accepted or expired, or those links will fail at Supabase.
