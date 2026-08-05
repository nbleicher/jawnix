# Production backup restore drill — 2026-08-05

## Decision

**PASS.** A current production database dump and Scraper Dataset were copied
from the VPS Restic repository into the independently encrypted Peely SSD
repository, restored from Peely with content verification, and exercised in
disposable local services. The versioned dataset older-rejection and
newer-replay operator paths also passed against a clone of the restored
production database.

No production database or active Scraper Dataset was modified during this
drill.

## Backup selection and external copy

Peely was mounted, but its newest snapshot was initially from 2026-07-27 and
did not include a Scraper Dataset. The current source snapshots were therefore
copied from the VPS repository into Peely with Restic's repository-to-
repository copy. The repositories use separate macOS Keychain credentials.

| Artifact | VPS snapshot | Peely snapshot | Timestamp / tag |
|---|---|---|---|
| PostgreSQL logical dump | `456263a4` | `baeeea43` | 2026-08-05 01:54:51 UTC; `database` |
| Scraper Dataset | `4beab3de` | `35945ffb` | 2026-08-05 01:56:54 UTC; `scraper-dataset`, `legacy-metadata-missing` |
| Dataset checksum | `7cc11670` | `11d8bb8e` | 2026-08-05 01:56:58 UTC; `scraper-dataset` |

The Peely repository then passed `restic check` across all 18 snapshots with
no errors.

## Restore evidence

Each selected Peely snapshot was restored with `restic restore --verify`:

- PostgreSQL dump: 628.143 MiB, one file verified. `pg_restore --list`
  returned 586 lines, and a full `pg_restore --exit-on-error --no-owner
  --no-acl` completed in a disposable PostgreSQL 18 container.
- Scraper Dataset: 2.596 GiB, three files verified. The restored
  `leads.db` SHA-256 matched the separately restored checksum:
  `7bba3b4f8b0aab3acc3e035aa5bfd36185180524e11d4c3c6a206541ab3ba55a`.
- The restored database was at Alembic revision `20260804_0044` and contained
  9,582,107 inventory leads, 5,550,075 distribution events, and 10,217 keyword
  history records.

The selected Scraper Dataset predates versioned publication metadata. Its
backup is correctly tagged `legacy-metadata-missing`; the drill verified its
independent checksum and did not fabricate publication metadata for those
bytes.

## Version-order paths

The restored production database was cloned inside the disposable PostgreSQL
container. Purpose-built versioned SQLite datasets and truthful metadata were
then used to exercise the operator CLI:

1. With synchronized version 2 recorded in PostgreSQL, a version 1 restore
   exited nonzero with `Restored Scraper Dataset is older than PostgreSQL's
   synchronized dataset version.` No apply was performed.
2. A version 3 restore with `--apply` returned `status: newer`,
   `replayRequired: true`, and `syncStatus: complete`.
3. PostgreSQL recorded complete publication version 3, one complete Inventory
   Sync Attempt, the restored phone, and a
   `scraper_dataset_restore_replayed` Audit Entry. The active SQLite checksum
   matched the version 3 metadata checksum
   `1c717bccf960e4186cae84cd0788e7a9355a76ae05fe72834d573645bbbb397c`.
4. Repeating the same apply returned `status: equal`,
   `replayRequired: false`, and `syncStatus: complete`, proving the recovery
   command is idempotent.

## Follow-up discovered

An independent attempt to run the full Alembic chain against a truly empty
PostgreSQL database failed at revision `20260803_0036`: revision 0001 creates
the current model's `ck_agent_billing_rate_required` constraint, then revision
0036 attempts to create it again. This does not invalidate the logical-dump
restore above, which restored at migration head. The fresh-bootstrap defect is
tracked separately in [issue #181](https://github.com/nbleicher/jawnix/issues/181).

The disposable PostgreSQL container was removed after the evidence was
recorded. The 3.2 GiB local restored payload was moved out of the workspace to
macOS Trash and remains recoverable until Trash is emptied. The encrypted
Peely snapshots remain available for recovery.
