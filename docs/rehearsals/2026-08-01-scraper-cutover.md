# Scraper Stage C cutover rehearsal — 2026-08-01

> ## RETRACTED — this report's GO decision is void
>
> Published as a GO and retracted the same hour. **Do not schedule a cutover
> from this artifact.** The rehearsal identified the wrong database as the
> production acquisition store, so its backup, its history artifact, and its
> quiescence finding all describe a database that is not production.
>
> The error: the worker box's `.env` names
> `postgres://gmaps@159.195.15.51:5432/gmaps_pro`, and the rehearsal treated
> that as the live store. It is not. Queried through the live legacy
> dashboard, the real acquisition store holds **779,408 businesses** properly
> distributed across PA, NC, OH, FL, SC, GA, TX, and UT. The database this
> report backed up holds **17,650 businesses, every one with an empty
> `state`**, its last enqueue is 2026-07-01, and the only client that
> connected to it during the rehearsal was the rehearsal's own probe. It is a
> stale or partial copy, not production.
>
> This report read that empty-`state` column as a production data-quality
> defect and preserved it as "parity." That was the tell, and it was
> misread. The real store's dashboard shows correct per-state counts.
>
> Corrected status of each blocker:
>
> | Blocker | Real status |
> |---|---|
> | 1 — acquisition backup | **Still open.** The dump is of the wrong database. The real store sits behind the Scale VPS (`51.81.184.162` / WireGuard `10.77.0.2`), which exposes only dashboard port 8090 and rejects every available SSH key. It remains unbacked-up and unreachable. |
> | 2 — history artifact | **Still open.** Built from the wrong database; the mechanism is proven but the contents are not production. |
> | 3 — rollover ownership | **Genuinely cleared** by PR #138, which is independent of this error. |
>
> What survives as valid evidence: the rollover ownership change and its
> tests, the additive migrations applying cleanly to a restored
> production-shaped database, the typed control service serving real data,
> and the vendored worker starting and connecting. The import and backup
> mechanisms are proven; only their inputs were wrong.
>
> Nothing was deployed and no production state was mutated. The
> `production-20260801T030809Z` tag created for this window was deleted.
>
> A replacement rehearsal must first obtain operator access to the Scale VPS,
> identify the DSN its dashboard actually uses, and re-run every data step
> against that store.

## Decision

~~**GO.**~~ **Retracted — see above.** Every release blocker from the
[2026-07-31 rehearsal](2026-07-31-scraper-cutover.md) was believed cleared;
two of the three were not.

Rehearsed Jawnix revision: `f94c06b` (`origin/main`, after PR #138).
Production mutation: none. All production access was read-only; one
custom-format `pg_dump` was written to the worker box and copied off-host.

## Topology (as recorded — the acquisition-store row is wrong)

Read-only inventory of all three hosts corrected the topology the previous
rehearsal assumed:

| Host | Role today |
|---|---|
| Application host (`159.195.15.51`, WireGuard `10.77.0.1`) | Jawnix stack and a `gmaps_pro` PostgreSQL 17.10 (host-level, bound publicly on 5432) — **wrongly identified below as the live acquisition store; it is a stale or partial copy** |
| Scale control VPS (`51.81.184.162`, WireGuard `10.77.0.2`) | Legacy Scale dashboard (`:8090` over WireGuard, live handshakes) and `gms-*` timers; holds the only OpenRouter key |
| Worker box (`152.53.209.52`) | Legacy worker containers (exited since ~2026-07-01), empty `scraper-db-1` PostgreSQL with volume `scraper_gmapsdev`, spool shipper |

The prior blocker "the acquisition PostgreSQL host has no restorable backup"
resolves as: the live acquisition store is the application host's `gmaps_pro`,
which had no dedicated backup; the worker box's `scraper-db-1` is empty and
was never the live store. The pipeline is fully quiescent: the last enqueue
was 2026-07-01, no worker has run since, and 5,006 `available` River jobs sit
unconsumed (they must be discarded before new workers start, or a month-late
scraping burst fires).

## Blocker 1 — verified acquisition backup

- `gmaps_pro-20260801T022539Z.dump` (custom format, `pg_dump` 17 via
  `postgres:17-alpine`, 54,449,776 bytes), SHA-256
  `8153294c0efb91f9c5c064c79fa0616bedd891d500b68dd7ea4604623617267e`.
- Off-host copies with matching digests: worker box
  `/root/db-backups/gmaps_pro/` and the operator Mac
  `~/jawnix-backups/gmaps_pro/`.
- `pg_restore --list` produced 129 TOC entries; restore into a disposable
  `postgres:17-alpine` completed without error.
- Exact reconciliation, restored vs. live production at report time —
  identical, confirming quiescence:

```text
enqueue_log      79,181 = 79,181
keyword_history     275 = 275
businesses       17,650 = 17,650
river_job        25,191 = 25,191
```

## Blocker 2 — keyword-history import artifact

- Built `keyword-history-20260801T022539Z.sqlite3` (6,496,256 bytes) from the
  verified restore: exactly the three source tables with the columns
  `import-keyword-history` requires. SHA-256
  `711ffd667338957d8c484bc4caa1e1eb4a7fd99ffb7cbcc9559efadbc5b7c326`.
- Import against a migrated Jawnix database (alembic head `20260731_0031`)
  reported the exact source counts (79,181 / 275 / 17,650), 97,106 candidate
  rows, 25 union terms, 64 records inserted.
- An identical rerun returned `skipped: true, inserted: 0, updated: 0`.

## Blocker 3 — rollover ownership

PR #138 moved automatic rollover into Jawnix: the control service's toggle is
a plain control-plane write, a typed `POST /api/keywords/rollover/events`
endpoint records outcomes, the acquisition-side executor and its timer are
deleted, and the Jawnix scheduler drives generation with the Jawnix-owned
provider. Live proof on restored production data: enable → event record →
disable all succeeded with **no OpenRouter configuration on the control
service**. Contract suite: `42 passed`. Backend suite: `534 passed, 14
skipped`, including six new rollover-runner tests.

## Additive migrations on restored production data

All seven pending control-service migrations
(`20260622100000-worker-heartbeats` … `20260727000000-dataset-publications`)
applied cleanly to the restored production database; the migration ledger
advanced 12 → 19 rows. This is the path the cutover takes; the prior
rehearsal's fresh-bootstrap enum failure (deviation 4) does not occur on the
restored path.

## Live typed smoke on restored production data

`scraper-control` at `f94c06b` served the restored database:
unauthenticated health 401, authenticated `{"status":"ok"}`; workspace
reported 4 active states / 17,650 businesses; keywords, winners, database,
coverage (summary and per-state), dashboard, history, and runtime all
returned 200 with production-shaped payloads.

Known production data condition (not a regression): every `businesses` row
has an empty `state` column, so `/api/database/states/{state}` returns 404
"Unknown database state". The legacy dashboard reads the same rows; parity is
preserved. Logged for post-cutover data repair.

## Worker evidence

The vendored worker image (`8e29dc9b`, built from the pinned source during
the 2026-07-31 rehearsal and reverified here) started against the restored
copy, connected, ran River maintenance, and idled with zero available jobs —
no live scraping was possible because the stale jobs were first discarded in
the disposable copy, which is exactly the planned window step. Host-level
heartbeat (`worker/heartbeat.py`, a systemd oneshot inspecting containers and
units) can only run on the real box and is verified during the window smoke.

## Rollback

The legacy stack is left completely untouched by the window: the Scale VPS
keeps running against the application host's original `gmaps_pro`, which the
new stack never writes. Rollback is one Jawnix `.env` repoint back to
`http://10.77.0.2:8090` plus a container recreate — demonstrated in reverse
by the window itself. Migrations are additive on a **copy**; the original
database is never migrated, so no downgrade can ever be needed.

## Remaining window prerequisites

1. `OPENROUTER_API_KEY` is set nowhere Jawnix-reachable; the only copy lives
   on the Scale VPS, which rejects the available SSH keys. Until the operator
   installs a key on the application host, generation and automatic rollover
   return their normal "AI generation is not configured" contract errors;
   every other function is unaffected.
2. WireGuard: `10.77.0.2` currently belongs to the Scale VPS. The window
   either serves `scraper-control` from the worker box over a new WireGuard
   peer, or temporarily over a restricted transport, before the Scale peer is
   retired with the legacy stack.
