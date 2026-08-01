# Scraper Stage C cutover rehearsal — 2026-07-31

## Decision

**NO-GO.** Production cutover must not be scheduled from this artifact.

The additive Jawnix migrations, vendored worker build, typed control contract,
Jawnix-owned generation, live read/write adapter paths, and legacy-dashboard
rollback all passed in isolated environments. The required acquisition-data
path could not be rehearsed: the acquisition PostgreSQL host has no restorable
database backup, and the latest documented Scraper Dataset snapshot contains
only the NPPES `leads` table rather than the three full-history source tables.

Rehearsed Jawnix revision: `db88a68` (`origin/main`, after S7 / PR #136).
Production mutation: none. Remote production inspection was read-only.

## Isolation and source artifacts

- Jawnix rehearsal project: `jawnix-scraper-rehearsal`; PostgreSQL had no
  published host port and used dedicated volumes.
- Acquisition rehearsal project: `scraper-acquisition-rehearsal`; PostgreSQL
  was loopback-only on port 55433 and used a dedicated volume.
- After the host-port collision described below was discovered, all remaining
  application/control traffic used Docker-internal service DNS.
- Latest remote Restic artifacts were snapshot `22c2ffd1` (Jawnix dump,
  2026-07-31 04:01 UTC), `f1d0fc71` (Scraper Dataset), and `905c0ad5`
  (dataset checksum).
- Restored dump SHA-256:
  `f42880f51ad693aef4a9e9b211101cdbdc248d0ef241c53dd189ddae27a958f3`.
- Restored Dataset expected/actual SHA-256:
  `7bba3b4f8b0aab3acc3e035aa5bfd36185180524e11d4c3c6a206541ab3ba55a`.

The acquisition host read-only inventory found `scraper-db-1` using only
Docker volume `scraper_gmapsdev`; there was no acquisition backup timer,
backup container, dump file, backup volume, or Restic snapshot. The Jawnix
Restic repository is not an acquisition PostgreSQL backup.

## Timings and verification

| Step | Wall time | Result / evidence |
|---|---:|---|
| List current remote Restic snapshots | 3.4 s | Latest Jawnix and legacy-metadata-missing Dataset snapshots identified. |
| Restore Jawnix dump bytes | 14.97 s | 462.547 MiB restored. |
| Restore Scraper Dataset bytes | 46.11 s | 2.596 GiB restored. |
| Restore Dataset checksum receipt | 2.01 s | 99-byte receipt restored; digest matched. |
| Restore Jawnix PostgreSQL via `ops/restore.sh` | 85.33 s | Completed without `pg_restore` error. |
| Apply Jawnix additive migrations | 1.46 s | Alembic advanced `20260729_0029` → `20260731_0031`. |
| Attempt history import from restored Dataset | 4.25 s | Correctly refused missing source tables; zero history/import rows committed. |
| Build vendored Go worker image | 136.60 s | Image `8e29dc9b3087…`, arm64, 60,337,715 bytes. |
| Fresh acquisition migration runner | 10.76 s | Failed on upstream enum migration; see deviations. |
| Typed `scraper-control` contract suite | 3.26 s | `44 passed` in pytest (1.96 s test time). |
| Build/start live `scraper-control` | 15.25 s | Authenticated health OK; unauthenticated health 401. |
| Build pinned legacy Scale image | 8.22 s | Built from `a57fcfadd387d068e706eec4925d368dd2fadd98`. |
| Stop new services/start legacy dashboard | 12 s | Legacy health, Dashboard, and Database all returned 200. |

Restored Jawnix counts before the Stage B migrations:

```text
alembic_version       20260729_0029
lead_inventory        9,244,326
lead_sources          9,541,530
distribution_events   4,588,287
customer_profiles     17
migration_audits      3
quarantined_rows      143,037
```

Restored Scraper Dataset verification:

```text
PRAGMA integrity_check  ok
leads rows              7,369,238
tables                  leads, sqlite_sequence, sqlite_stat1
```

The full-history command refused the artifact with:

```text
Scraper keyword-history source is missing required columns
(enqueue_log: keyword, enqueued_at or day;
 keyword_history: keyword, last_enqueued;
 businesses: first_seen, keyword, last_seen)
```

After refusal, both `keyword_history` and `keyword_history_imports` remained at
zero.

## Functional evidence on seeded acquisition data

Because no acquisition backup existed, this section used the contract suite's
canonical current-schema fixture, not restored production acquisition data.
It is supporting evidence only and does not clear the no-go.

- `scraper-control` returned 401 without a bearer token and `{"status":"ok"}`
  with the rehearsal token.
- Workspace reported four active states, two initial keywords, five businesses,
  and no running workers.
- Reads passed for keywords, database, coverage, monitoring dashboard, runtime,
  history, Source Segments, and Dataset publication.
- The OH export returned two data rows with a single CSV header.
- Keyword preview/save added `roofers`, wrote a new version, and created the
  enqueue trigger.
- Pipeline pause/drain and resume both passed.
- Runtime preview/save passed with an unchanged 806-cell configuration and an
  enqueue trigger.
- Jawnix reached `scraper-control` by internal DNS and returned connected reads
  for database, coverage, monitoring, keywords, history, and runtime.
- Jawnix generated a 25-term draft through a deterministic local OpenRouter
  fake, previewed and saved it, enqueued it, and marked one draft accepted.
- Jawnix exported OH CSV with status 200, `no-store`, the expected attachment
  filename, and two data rows.
- Jawnix pipeline pause/resume and runtime scheduled save passed; eleven
  Scraper audit entries were recorded in the rehearsal database.

## Rollback demonstration

The rehearsal stopped the new Jawnix API, `scraper-control`, and local model
fake. It then started the pinned Scale dashboard image from
`scraper-source-baseline-2026-07-27-v4` against the same seeded acquisition
database. Within 12 seconds:

```text
legacy /healthz    {"status":"ok"}
legacy /dashboard  200
legacy /database   200
new Jawnix API     stopped
new scraper-control stopped
```

Workers were not started during either side of the seeded rollback because a
synthetic queue plus live network access could initiate real scraping. The
production-data rehearsal must include worker/timer stop/start and heartbeat
proof after a restorable acquisition backup is available.

## Deviations and blockers

1. **No acquisition PostgreSQL backup (release blocker).** The live host had
   only the active Docker volume. Create an encrypted off-host backup job,
   verify a current dump, and rehearse its restore.
2. **History source mismatch (release blocker).** The restored Dataset is a
   `leads`-only SQLite database. `import-keyword-history` is SQLite-only and
   requires `enqueue_log`, `keyword_history`, and `businesses`, while the live
   acquisition store is PostgreSQL. Produce a verified immutable import
   artifact or extend the command to import from the restored acquisition DB,
   then reconcile counts/checksum and prove idempotency.
3. **Automatic rollover ownership mismatch (release blocker).** Jawnix
   generation succeeded with the app-host-only key, but enabling rollover
   returned `422 AI generation is not configured.` `scraper-control` still
   gates rollover on its own OpenRouter key, contradicting S7's ownership rule.
4. **Fresh bootstrap migration failure.** On an empty PostgreSQL 16 database,
   the combined runner failed with `unsafe use of new value "pending" of enum
   type river_job_state`. The required restored-production path could not be
   tested because blocker 1 removed its baseline.
5. **Host-port collision / near miss.** Port 18090 was already a production
   WireGuard SSH tunnel. Two GETs (`/healthz` and `/api/workspace`) reached it;
   the first mutation-capable script aborted at the 404 before any write. All
   subsequent calls used Docker-internal DNS. The runbook now requires listener
   inspection and forbids host-port service calls in rehearsal.
6. **Fixture-only read grants.** The canonical test fixture created views after
   the read-only role's initialization grants, so the rehearsal granted SELECT
   on those fixture objects. This is not evidence about production grants and
   must be verified after a real restore.

## Exit criteria for a replacement GO report

- Current acquisition PostgreSQL dump exists off-host, matches its receipt,
  restores cleanly, and reconciles source counts.
- All additive migrations pass on that restore and both ledgers are complete.
- Full-history import reports the three exact source counts and checksum; an
  identical rerun is skipped with zero inserts/updates.
- Automatic rollover enables/disables with no acquisition-host OpenRouter key.
- Typed contract and Jawnix live functional checks pass against restored data.
- New and legacy worker/timer stop/start plus heartbeats are demonstrated.
- Rollback returns the legacy stack to service without downgrading migrations.
