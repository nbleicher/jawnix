# Implementation acceptance record

Validated on 2026-07-24 against the isolated `vps-batch-platform` worktree and
`https://staging.jawnix.com`. Production DNS, Railway, the legacy Supabase
project, and the read-only `dat` source remain unchanged.

## Passed

- The protected Git bundle, annotated rollback tag, encrypted Railway export,
  encrypted Supabase application-data export, and source checksum manifest
  reverified. The bundle records a complete Git history.
- The attached `dat` directory was used only as migration input. Its manifest,
  SQLite database, CSVs, history, configuration, and scripts were not edited,
  moved, or deleted.
- Python compilation, Ruff, dependency checks, and 19 automated tests passed.
  Docker images build with Python 3.12 and PostgreSQL 18 client tools.
- Caddy, FastAPI, PostgreSQL 18, the worker, scheduler, and backup worker are
  running on the VPS. `healthz` and `readyz` pass over HTTPS and report billing
  disabled. PostgreSQL is private to the Docker network.
- The new Supabase Auth project is in `us-east-1`. Sixteen Auth/profile UUIDs
  are synchronized, including nine legacy identities. All eight approved
  customer mappings are confirmed against the Summit agency:
  `noah`, `jack`, `jo`, `max`, `tim`, `spencer`, `matthew`, and `ali`.
- A legacy-UUID login exchanged a Supabase token for the secure VPS session,
  loaded the mapped profile, and saved licensed-state data through the VPS API.
- The production-size import reconciled to:

  | Destination | Rows |
  |---|---:|
  | Unique inventory phones | 8,579,356 |
  | Source provenance | 8,870,024 |
  | Distribution events | 4,588,285 |
  | Customer profiles | 16 |
  | Migration audits | 2 |
  | Quarantined rows | 115,875 |

- Manifest import processed 6,657,590 CSV-aware source rows: 6,564,999 valid
  inventory phones and 92,591 quarantines. Scraper import processed 5,735,955
  rows into 2,305,025 valid distinct phones; 290,668 overlapped the manifest
  and 2,014,357 extended inventory. The manifest won every overlap.
- All 177 history files copied with aggregate SHA-256
  `24e4f385ad23f91b8d4f361c56802f60ea7b63590181ac2eec09dc9077e2c72d`.
  Eighty-eight unambiguous recipient files were imported and 89 ambiguous
  files were safely skipped.
- Two concurrent 50-row allocations produced 100 unique distribution events
  with no overlap. A rollback-only 100,000-row allocation and CSV generation
  against the 8.58-million-row staging inventory completed in 80.14 seconds,
  returned exactly 100,000 unique phones, and left no acceptance data behind.
- Telegram request notification and the authorized approval workflow operate
  against the staging webhook. A live two-row end-to-end request generated one
  `phone,title` CSV and Resend accepted delivery from
  `Jawnix <hai@jawnix.com>`.
- The nightly scraper synchronization uses an immutable SQLite snapshot and
  skips unchanged sources by checksum. The initial source synchronized and a
  completed scraper run was recorded.
- Restic database/base and WAL snapshots completed independently, including
  database snapshots `aea53f8e` and `523cb85a` and WAL snapshots `9b1dfd2f`
  and `8e9cac95`. Repository integrity passed across all seven snapshots.
- The logical dump in Restic snapshot `aea53f8e` restored into a disposable
  PostgreSQL 18 database and exactly reproduced 8,579,356 inventory phones,
  4,588,285 events, 8,870,024 provenance rows, 16 profiles, and two audits.
- The rollback-only acceptance data, disposable databases, temporary restore
  files, and synthetic CSV artifacts were removed after verification.

## Deferred before production cutover

- Copy the verified VPS Restic snapshots into the second encrypted repository
  at `/Volumes/Peely SSD/Jawnix Backups`. The launch agent and pull script are
  installed; this copy was explicitly deferred so the Mac could be closed.
- Register the Resend webhook for `email.delivered`, `email.bounced`,
  `email.complained`, and `email.failed`. The staging API key is send-only and
  cannot administer webhooks; use a full-access key or the Resend dashboard.
  The endpoint and signature-validation tests are already implemented.
- Supply the collector command in `JAWNIX_SCRAPER_COMMAND` if the VPS must run
  the upstream scraper itself. With the current blank value, the scheduler
  synchronizes a changed scraper database but does not execute collector code.
- Activate/reset passwords for migrated customers before production. Supported
  Supabase administration preserves their UUIDs but cannot import legacy
  password hashes.
- Rotate every credential shared during staging: VPS/root, Cloudflare,
  Supabase, Telegram, Resend, PostgreSQL/session/encryption, and Restic.
- Run the final source/application delta import, switch production DNS only
  with explicit approval, execute the production smoke test, and complete the
  48-hour observation window. Railway and legacy Supabase tables must remain
  available throughout that window.
