# Offsite backups to Cloudflare R2

`ops/backup.sh` copies every local Restic snapshot to a second, R2-backed
Restic repository after each successful local backup. R2 is S3-compatible and
has zero egress fees, so restores cost only request operations. The copy is
fail-soft: without `R2_RESTIC_REPO` the backup behaves exactly as before, and
a failed copy exits non-zero while leaving the local repository intact.

This supersedes the Mac pull job (`ops/macos-backup-pull.sh`) as the primary
offsite copy; the Mac job is retained as an optional third copy.

## 1. Create the R2 bucket

1. Cloudflare dashboard → R2 → Create bucket.
2. Name: `jawnix-restic-backups`. Location: automatic. This bucket is
   private; do not enable public access.

## 2. Create the R2 API token

1. R2 → Manage R2 API Tokens → Create API token.
2. Permissions: **Object Read & Write**.
3. Specify bucket(s): `jawnix-restic-backups` only.
4. Save the **Access Key ID** and **Secret Access Key**. The account ID is in
   the R2 overview URL / sidebar (`<accountid>.r2.cloudflarestorage.com`).

## 3. Configure the production host

The `backup` compose service loads `/srv/jawnix/app/.env` via `env_file`, so
append:

```sh
R2_RESTIC_REPO=s3:<accountid>.r2.cloudflarestorage.com/jawnix-restic-backups
AWS_ACCESS_KEY_ID=<r2 access key id>
AWS_SECRET_ACCESS_KEY=<r2 secret access key>
```

Optional: set `R2_RESTIC_PASSWORD` to encrypt the R2 repository with a
different password than the local one. When unset it defaults to
`RESTIC_PASSWORD`, so a single password unlocks both copies.

Recreate the backup container so it picks up the new variables:

```sh
docker compose up -d backup
```

## 4. Seed the first copy

Run one backup manually; it initializes the R2 repository and copies all
current snapshots (the initial seed transfers the full ~47 GB):

```sh
docker compose exec backup /app/ops/backup.sh
```

## 5. Verify

```sh
docker compose exec backup sh -c 'restic -r "$R2_RESTIC_REPO" snapshots'
docker compose exec backup sh -c 'restic -r "$R2_RESTIC_REPO" check'
```

Snapshot IDs differ between repositories; compare timestamps, tags, paths,
and sizes instead. Nightly backups now copy automatically; watch the backup
logs for `Offsite copy to R2 completed` or `OFFSITE BACKUP FAILED`.

## 6. Restore from R2

From the backup container (or any host with restic, the token, and the
password), restore the latest database dump and feed it to `ops/restore.sh`:

```sh
docker compose exec backup sh -c \
  'restic -r "$R2_RESTIC_REPO" restore latest --tag database --target /srv/jawnix/backups/r2-restore'
RESTORE_CONFIRM=YES /app/ops/restore.sh /srv/jawnix/backups/r2-restore/srv/jawnix/backups/jawnix-<timestamp>.dump
```

For a host loss, bring up the stack, set the three R2 variables in `.env`,
restore the dump, WAL archive, and Scraper Dataset snapshots with
`restic -r "$R2_RESTIC_REPO" restore`, then follow the standard restore
procedure in OPERATIONS.md.
