# Scraper Stage C cutover rehearsal — 2026-08-01

## Decision

**GO.** Every release blocker from the [2026-07-31 rehearsal](2026-07-31-scraper-cutover.md)
is cleared with verified evidence. The production cutover window may be
scheduled from this artifact.

Rehearsed Jawnix revision: `f94c06b` (`origin/main`, after PR #138).
Production mutation: none. All production access was read-only; one
custom-format `pg_dump` was written to the worker box and copied off-host.

## Corrected production topology

Read-only inventory of all three hosts corrected the topology the previous
rehearsal assumed:

| Host | Role today |
|---|---|
| Application host (`159.195.15.51`, WireGuard `10.77.0.1`) | Jawnix stack **and the live `gmaps_pro` PostgreSQL 17.10** (host-level, bound publicly on 5432) |
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
