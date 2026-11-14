# Legacy host decommission (159.195.15.51)

Preparation runbook for retiring the old Jawnix VPS. Nothing here has been
executed destructively; `ops/legacy-decommission.sh` defaults to a dry run and
only acts with `--apply`, after explicit approval.

## Which host is "the legacy host"

- **Legacy host: `159.195.15.51`** (netcup, hostname `v2202605362710464443`),
  reachable as `root@159.195.15.51` with `~/.ssh/jawnix_vps` (ssh config alias
  `jawnix-legacy`). It holds the retired 15 GB `jawnix` database
  (`jawnix-postgres-1`) and `jawnix-caddy`, and shares the box with the
  unrelated `buzz-prod-*` tenant stack, which owns ports 80/443.
- **Production host: `152.53.209.52`** (hostname `textro`) — do not touch its
  configuration. It receives only the final dump copy.
- **`51.81.184.162`** is the old OVH Scraper control VPS, *not* this legacy
  host. It currently refuses SSH (connection refused; ICMP only). Its live
  acquisition store was migrated off it in the 2026-08-04 cutover, so nothing
  in this decommission depends on it. If it is ever to be decommissioned too,
  that must happen through the OVH provider panel while SSH is down — out of
  scope here.

## Verified evidence (refreshed 2026-11-12)

- `jawnix-postgres-1` (postgres:18, healthy) — database `jawnix`, **15 GB**,
  **zero connections** besides the read-only probe itself
  (`pg_stat_activity` count excluding `pg_backend_pid()` = 0). Recent log
  entries show only routine checkpoints; no client traffic.
- `jawnix-caddy` (caddy:2.10-alpine) publishes **no ports**; its Caddyfile is
  only a loopback-style reverse proxy of `:8080`/`:8081` to the production
  host, and the edge (`buzz-prod-caddy-1`, the port owner) no longer routes
  to it except for the jawnix.com/staging/scraper sites — which resolve
  directly to production anyway (see DNS below).
- Stopped legacy app containers still present: `jawnix-api-1`,
  `jawnix-worker-1`, `jawnix-scheduler-1`, `jawnix-metrics-worker-1`,
  `jawnix-backup-1` (all exited ~5 weeks ago), `jawnix-migrate-1`.
- Summit Metrics is already gone from the edge: the `metrics.jawnix.com`
  block was removed from the buzz Caddyfile on 2026-11-10 (backup saved at
  `/root/buzz/deploy/compose/Caddyfile.pre-remove-metrics-20261110T234710Z`)
  and no `summit-metrics-*` container exists.
- DNS: `jawnix.com`, `www`, `staging`, `scraper.jawnix.com` →
  `152.53.209.52`. Only `metrics.jawnix.com` → `159.195.15.51` remains.

## Finding: Mac external-backup pull targets the stale legacy repository

`ops/macos-backup-pull.sh` (and the copy installed at
`~/Library/Application Support/Jawnix/`) defaulted to
`root@159.195.15.51:/srv/jawnix/restic-repository`. That repository's data
stops around 2026-09-28 (last dumps in `/srv/jawnix/backups`); the live
repository (56 GB, current) is on production at the same path. The daily
launch agent therefore mirrored a frozen repository — silently "successful"
staleness. The defaults in `ops/macos-backup-pull.sh` and `.env.example` are
updated to the production host in this change. The launch agent log currently
shows only "Peely SSD is not mounted" failures, so the pull is inert locally
until the drive returns regardless.

## Execution sequence

1. Run the evidence/dry-run pass and review output:

   ```sh
   ops/legacy-decommission.sh
   ```

2. After approval, execute:

   ```sh
   ops/legacy-decommission.sh --apply
   ```

   This takes the final custom-format dump, checksums it, verifies it with
   `pg_restore --list`, streams it to
   `root@152.53.209.52:/srv/jawnix/backups/jawnix-legacy-final-<ts>.dump`,
   re-verifies checksum and integrity on production, then stops and removes
   `jawnix-caddy`, `jawnix-postgres-1`, and the six stopped app containers.
   It aborts before any removal if a connection appears or a checksum
   mismatches. `buzz-prod-*` is never touched.

3. Delete the DNS record (manual — no API token is provisioned):
   1. Open https://dash.cloudflare.com and select the **jawnix.com** zone.
   2. Go to **DNS → Records**.
   3. Locate the **A** record: name `metrics`, content `159.195.15.51`.
   4. **Edit → Delete → Confirm**.
   5. Verify `dig +short metrics.jawnix.com` returns nothing.

4. Repoint the Mac external-backup pull at production (manual):
   1. Authorize the dedicated read-only key on production:
      `ssh-copy-id -i ~/.ssh/jawnix_vps.pub root@152.53.209.52` (or append to
      `/root/.ssh/authorized_keys`).
   2. Confirm the Keychain item `jawnix-vps-restic-password` matches the
      *production* Restic password (it currently unlocked the legacy repo;
      the two were generated independently per OPERATIONS.md).
   3. Reinstall the agent: `ops/install-macos-backup.sh`, then run one manual
      pull with the Peely SSD mounted and confirm new snapshots appear and
      `restic check` passes.

5. Leave Docker volumes (`jawnix_postgres_data`, `jawnix_caddy_data`,
   `jawnix_caddy_config`) and `/srv/jawnix` on the legacy host intact for a
   short observation window, then remove them manually if desired:

   ```sh
   ssh jawnix-legacy 'docker volume rm jawnix_postgres_data jawnix_caddy_data jawnix_caddy_config'
   ```

6. The legacy VPS itself (and the shared `buzz-prod-*` tenant) stays up;
   cancelling the netcup server is a provider-panel decision for the user,
   outside this runbook.

## Rollback

Until the volumes are removed, rollback is `docker compose up -d postgres
caddy` from `/srv/jawnix/app` on the legacy host. After volume removal,
rollback is a restore of the verified final dump from
`/srv/jawnix/backups/jawnix-legacy-final-<ts>.dump` on the production host.
