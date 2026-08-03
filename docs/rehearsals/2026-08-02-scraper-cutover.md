# Scraper Stage C cutover rehearsal — 2026-08-02

## Decision

**GO.** All three release blockers are cleared against the **real** acquisition
store, which this rehearsal located and gained operator access to for the first
time. Supersedes the [2026-07-31 NO-GO](2026-07-31-scraper-cutover.md) and the
[retracted 2026-08-01 GO](2026-08-01-scraper-cutover.md).

Rehearsed Jawnix revision: `ac66153` (`origin/main`).
Production mutation: none. The only write to production infrastructure was
appending an operator SSH public key to `~ubuntu/.ssh/authorized_keys` on the
control VPS. Every functional test ran against a restored copy.

## The acquisition store, correctly identified

The retracted report treated the application host's `gmaps_pro` as production.
It is not. The real store is a **loopback-bound Docker PostgreSQL 16.14 on the
Scale control VPS itself** (`51.81.184.162`, WireGuard `10.77.0.2`), container
`gms-scale-db-1`, DSN `postgresql://postgres@127.0.0.1:5432/gmaps_pro`. It
publishes no host port beyond loopback, which is why every earlier probe from
the application host and the worker box failed to find it.

Access was obtained through the OVH Serial-over-LAN console: the image is
Ubuntu, which disables root login and provisions an `ubuntu` sudo account. The
operator password authenticates that account, not root — the reason every
earlier `root@` attempt was rejected. `~/.ssh/scale_vps` is now installed for
`ubuntu` with passwordless sudo.

| Measure | Real store | Database the retracted report backed up |
|---|---:|---:|
| businesses | 779,585 (all with `state`) | 17,650 (all `state` empty) |
| enqueue_log | 3,810,323 | 79,181 |
| keyword_history | 13,377 | 275 |
| size | 3,266 MB | 183 MB |
| last enqueue | 2026-08-03 00:21 UTC | 2026-07-01 |

**The pipeline is live, not quiescent.** Eight workers (`gms-scale-worker-1`
… `-8`) are running, alongside `gms-serve`, `gms-enqueue`, and the alert,
database-cache, dataset-publication, export, heartbeat, keyword-rollover,
ship, and uptime timers. The cutover window must stop these deliberately; the
retracted report's claim that nothing was running was an artifact of reading
the wrong database.

## Blocker 1 — verified acquisition backup (cleared)

- `gmaps_pro-REAL-20260803T013211Z.dump`, custom format, 262,037,742 bytes,
  SHA-256 `1c310b8f451f044850f42b8b82fd71aebceb4ad03132ba6faa03100f374d35a7`.
- Off-host copy on the operator Mac with a **matching digest after transit**.
- `pg_restore --list` produced 167 TOC entries; a parallel restore into a
  disposable `postgres:16` completed with exit 0.
- Restored copy reconciles **exactly** against the live source:

```text
businesses       779,585 = 779,585   (779,585 with a non-empty state)
enqueue_log    3,810,323 = 3,810,323
keyword_history   13,377 = 13,377
leads                  6 = 6
river_job         20,153
```

## Blocker 2 — keyword-history artifact (cleared)

- `keyword-history-REAL-20260803T013211Z.sqlite3`, 331,866,112 bytes, SHA-256
  `b27434ac71f79e63499c7ab452431506f8bcf71b29108fcbeda694e271f403d6`, built by
  streaming the three source tables out of the verified restore.
- Import into a Jawnix database at alembic head `20260731_0031` reported the
  exact source counts — 3,810,323 + 13,377 + 779,585 = **4,603,285 source
  rows** — yielding **1,659 union terms** and 4,954 persisted records.
- Identical rerun: `skipped: true, inserted: 0, updated: 0`, with
  `keyword_history` still at 4,954 and one import ledger row.

The 1,659 historical terms are the exclusion set real generation depends on;
the retracted artifact carried 25.

## Blocker 3 — rollover ownership (cleared)

PR #138 moved rollover into Jawnix. Proven here against restored real data with
**no OpenRouter configuration whatsoever on the control process**: enable →
record event → disable all succeeded. Contract suite `42 passed`; backend suite
`535 passed, 14 skipped`.

## Migrations — nothing pending

The real store keeps two ledgers, `scraper_migrations` (7 upstream) and
`scale_migrations` (12). Its `scale_migrations` set matches the repository's
`scraper/scraper_changes/migrations/` exactly, through
`20260727000000-dataset-publications.sql`. **Zero additive migrations to apply
at cutover.** The fresh-bootstrap enum failure recorded on 2026-07-31 is not on
this path.

## Typed smoke on restored real data

`scraper-control` at `ac66153` against the restored copy: unauthenticated
health 401, authenticated `{"status":"ok"}`; workspace reported 4 active states
(OH, KY, MO, AZ), 25 keywords, 779,585 businesses. All twelve read endpoints
returned 200 — keywords, winners, database, per-state database detail,
coverage summary/state/keywords/cells, dashboard, history, runtime, and source
segments.

Parity against the live legacy dashboard for Ohio, sampled minutes apart while
the pipeline ran:

```text
legacy dashboard   OH  136,636 businesses  114,659 phones  1,506 niches
new typed API      OH  136,677 businesses  114,688 phones  1,506 niches
```

The small deltas are live ingest between the two reads, not drift.
`/api/database/states/{state}` returns 200 here; its 404 in the retracted
report was caused entirely by the wrong database's empty `state` column, and
there is **no production data-quality defect**.

Write paths on the copy: preview of the real 25-keyword list plus one probe
term returned a correct 26-term diff, and the compare-and-save applied it.

Rollover reports `state: "ready"` at 20,150 / 20,150 posted jobs — the current
campaign has genuinely drained, so a rollover is operationally due. The last
recorded automatic rollover was 2026-07-27.

## Rollback

The window never migrates or writes the live store, so no downgrade path is
required. Rollback is: stop the new control service and workers, restart the
legacy `gms-*` units and worker containers, and repoint Jawnix's
`JAWNIX_SCRAPER_OPS_URL` back to `http://10.77.0.2:8090`. The verified dump
above is the backstop if the database itself is ever damaged.

## Window prerequisites, both now satisfiable

1. `OPENROUTER_API_KEY` exists on the control VPS at `/opt/gms-scale/.env`
   (`sk-or-v1-…`, model `deepseek/deepseek-v4-flash`). It must be **moved** to
   the Jawnix application host and removed from the acquisition host, per the
   S7 ownership rule.
2. WireGuard `10.77.0.2` is the control VPS, which is also where the database
   and the new stack will run — so `scraper-control` binds there directly and
   no new peer is needed. This is simpler than the retracted report assumed.

## Exit criteria from the 2026-07-31 report

| Criterion | Status |
|---|---|
| Current dump off-host, matches receipt, restores, reconciles | ✅ exact |
| Additive migrations pass, both ledgers complete | ✅ none pending; both ledgers current |
| History import exact counts; identical rerun skipped | ✅ 4,603,285 rows; rerun skipped |
| Rollover without acquisition-host OpenRouter key | ✅ |
| Typed contract and live functional checks on restored data | ✅ 42 + 535 tests, 12 endpoints |
| Worker/timer stop-start and heartbeats demonstrated | ⚠️ deferred to the window — the live stack is serving production and must not be cycled outside it |
| Rollback returns legacy stack without downgrading migrations | ✅ no migrations applied; legacy stack untouched |
