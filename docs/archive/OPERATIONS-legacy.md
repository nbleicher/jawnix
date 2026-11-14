# Retired Railway/Supabase-era operations

These runbooks were retired from [../OPERATIONS.md](../OPERATIONS.md) once the
rollback windows they protected had expired: Railway is decommissioned as a
deployment target, and the legacy Supabase project was retired as the identity
provider (Better Auth is live; see ADR "Replace retired Supabase authentication
with Better Auth"). They are kept verbatim as the record of how the 2026-07
VPS cutover was protected. Nothing below is an active procedure.

## Protected baseline

The pre-refactor production baseline is commit `119d072bcf1a3ccc961847702e8a16ce1c109d52`, tagged and pushed as `backup/pre-vps-batch-platform-20260721T190329Z`. The linked feature worktree was at `6a338a184b555dcab97680088f3a6b59af0cfd4d`.

The verified local recovery set is outside Git at:

```text
.context/backups/20260721T190329Z/
```

It contains a verified `git bundle --all`, source archives, encrypted Railway variables, an encrypted Supabase application-data export, source checksums, and restore instructions. Its encryption password is in the macOS Keychain service `jawnix-backup-20260721T190329Z`, account `noahbleicher`. Supabase Auth was not exported or changed.

Do not stop Railway or delete old Supabase application tables during provisioning, migration, cutover, or the first 48 hours after cutover.

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
