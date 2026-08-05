# Scraper production ownership cutover — 2026-08-04

## Decision

**GO — completed.** The acquisition cutover, data reconciliation, typed
control verification, application recovery, scheduled-control checks, and
resumed-pipeline observations passed. Jawnix now owns the acquisition source,
control plane, workers, schedules, keyword history, rollover decision, and
model credential. The legacy Scale stack is stopped and retained only for the
seven-day rollback period.

Cutover source: `scraper-cutover-complete-20260804T234259Z`, commit
`e1a55e29839581d7300351929025db5d4d8c69b7`. The preparatory cutover source was
`scraper-cutover-20260804T230235Z`, commit
`21d79ff3caac9094b70cd7152f37149fab74a885`.

## Immutable backups and rollback material

The window paused acquisition before freezing the following artifacts. The
database dump was listed with `pg_restore`, restored into disposable
PostgreSQL 16, and reconciled exactly. Both acquisition artifacts were copied
off-host to the operator workstation and their digests matched after transit.

| Artifact | Location / identifier | SHA-256 or result |
|---|---|---|
| Acquisition PostgreSQL dump | `/opt/gms-backups/cutover-20260804T230356Z/acquisition-gmaps_pro-20260804T230356Z.dump` | `6498a4991f92702937756a79d87f75c07b6f89f46bab53ce8c036d15076a7e41`; 182 TOC entries |
| Legacy Scale source and configuration | `/opt/gms-backups/cutover-20260804T230356Z/legacy-gms-scale-20260804T230356Z.tar.gz` | `794605f8d5f6f37747337f0de954551c2bf276dbbcc17469f2e88ac95a0ac213` |
| Immutable keyword-history SQLite | `keyword-history-20260804T230356Z.sqlite3` | `1b7e6e22317059463e6bcaa08a4a89ad7440e495cc4c5f7954e0b932aee85a62` |
| Jawnix database Restic snapshot | `c96b888b` | fresh during window |
| Jawnix WAL Restic snapshot | `761d389f` | fresh during window |
| Scraper Dataset Restic snapshot | `773fb00f` | dataset checksum `a0c71b8c` |
| Restic integrity | 129 snapshots | `restic check` passed with no errors |

The prior worker, dashboard, and temporary control images are retained as
rollback images with digests `c2be01ef…`, `564df6ff…`, and `d9ddb906…`.
The stopped legacy dashboard and complete legacy source/configuration archive
remain available. Do not delete them before **2026-08-11 23:43 UTC**.

## Data reconciliation and migration

The quiescent acquisition source contained 960,366 `businesses`, 5,166,973
`enqueue_log` rows, 19,936 `keyword_history` rows, and 6 `leads`. It had no
active River jobs. The disposable restore matched all four counts exactly.

The additive acquisition migration added
`20260730000000-cache-database-totals.sql`; both materialized database views
exist and all four source counts remained unchanged. The Jawnix application
database is at Alembic head `20260804_0044`.

The immutable keyword-history artifact contained 6,147,275 source rows. Import
reported 2,853 normalized union terms and 8,482 inserted records. An identical
rerun reported `skipped: true`, `inserted: 0`, and `updated: 0`; the import
ledger record and checksum were verified.

Before the cutover, the fixed shipper drained 6,705 stranded NDJSON/done pairs
into the sharded `archive-v2` layout, accounting for 5,905 rows and leaving the
live spool at zero. The legacy archive is retained in place.

## Deployed services and credentials

The vendored worker binary checksum is
`3ef07d9aa4a972a22fbe152356de4633214bc78bfb42e93610afe8686c52c715`.
Eight `gmapssaas:local` workers run image
`012e88a3290340017bf395d3e64d068fcf6fd9f0e59562fc03c86053362e8e31`.
The typed control image was initially built as
`52fd3088208ae32ea16db1d84fc0ef328da15357383fec1dc5c650d67dc2ef08`
and rebuilt from the completion revision as
`7b8fbda396e1babb7f86d064f94a56719cd11aa4929ecd2238c274bd1183f168`
after the production-scale query fix. `gms-enqueue.service` remains active in
watch mode when the fully covered campaign produces no jobs.

No active acquisition-host environment or running container contains an
OpenRouter credential. The credential exists only on the Jawnix application
host; rollover enable/disable was exercised through the control API and the
original state was restored.

## Verification

- CI passed on PRs #175, #176, and #177. The final run passed backend,
  frontend typecheck/unit/e2e, Docker image, scraper-control contract, and
  scraper-worker checks. The local production-control suite passed 45 tests.
- Authenticated production reads returned 200 for health, workspace, keywords,
  winners, database summary/detail/export inputs, coverage summary/detail,
  dashboard/activity/stack, history, runtime, source segments, and dataset
  publication. The 5.16-million-row history query completed successfully after
  deployment.
- Safe writes passed for keyword preview/save/enqueue, runtime preview/save,
  rollover toggle/restore, and a 72,836-byte production CSV export. The final
  pipeline write resumed acquisition.
- After resume, queue depth and running work changed on successive samples,
  completed work advanced, and `scrape_results` increased from 4,823,958 to
  4,826,322. This is direct queue-movement evidence, not an inferred service
  state.
- The latest heartbeat reported 8/8 workers running, zero unhealthy, control
  and queue APIs healthy, all required services healthy, and an empty spool.
- All nine Jawnix-owned timers/path units are active. A manual database-cache
  refresh completed both materialized views in 6.6 and 7.0 seconds. The
  authenticated publication endpoint returned 200 from inside the Jawnix API
  container.
- The application host was restarted during the window. Its persistent
  volumes returned intact, all six Compose services recovered, PostgreSQL was
  healthy, migration head remained current, and public health/readiness passed
  independently from the worker host.

## Production discoveries

The window found and corrected four issues rather than accepting false smoke
results:

1. Publication/cache schedules and related Jawnix-owned source were missing
   from `main`; PR #175 added them.
2. Archive sharding, heartbeat inventory, Nightly bearer authentication, and
   stale frontend assertions blocked a safe cutover; PR #176 corrected them.
3. `enqueue.py --watch` exited when a campaign was fully covered, causing a
   systemd restart loop; PR #177 keeps watch mode alive.
4. Campaign History used `lower()` on both sides of a 5.16-million-row join,
   bypassing the composite index; PR #177 uses the already-normalized state
   equality and restored the endpoint.
5. The application host still had unmanaged June 8–22 scraper/Playwright
   sessions targeting its obsolete partial scraper copy. Stopping those
   closing sessions and restarting the host reclaimed roughly 10 GiB of
   available memory; their binaries and data were not deleted. Acquisition now
   runs only on the acquisition host.
6. WireGuard had no persisted application-side `ListenPort`, so an application
   reboot selected a random UDP port and temporarily broke the control path.
   The known-working port `46039` is now explicit in
   `/etc/wireguard/wg0.conf`, allowed only from the acquisition host, with the
   prior configuration retained as
   `wg0.conf.pre-scraper-cutover-20260805`. A new handshake and bidirectional
   private ping passed after the change.

The first post-recovery automatic rollover attempt authenticated to the typed
control, called OpenRouter three times from the application host, and recorded
an audited `generation_failed` outcome because the model did not return 25
sufficiently distinct terms against 2,853 exclusions. This was not a missing
credential or cross-host ownership failure. The scheduler retained the current
campaign and did not invent or activate an invalid list. The final no-op save
then resumed the current campaign at 8,033 of 20,150 coverage jobs; rollover
correctly reports `working`, so the scheduler will reconsider generation only
after this campaign drains.

The in-app signed-in browser was unavailable during the window, so the final
control writes use the authenticated typed API rather than the admin UI. No UI
audit entry is claimed for those operator calls. Repository contract and audit
tests remain green; this deviation is explicit rather than fabricated.

## Rollback

Until 2026-08-11 23:43 UTC, rollback is to pause acquisition, stop the new
workers/timers/control service, restore the prior Jawnix release and endpoint
configuration, then restart the retained legacy Scale dashboard, images, and
units against the existing additive database. Do not downgrade migrations or
merge data automatically. Preserve the failed release's databases, logs,
checksums, and import report for reconciliation.
