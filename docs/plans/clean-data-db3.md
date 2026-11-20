# Build plan: Clean data (DB3)

Status, updated 2026-11-20:
- **P0 is done.** The match script is written, and a one-state pilot passed with 0 rejected rows (see "P0 result").
- **Jawnix never scrubs.** All scrubbing happens in Textro. Jawnix only matches and uploads the clean files.
- **P1 and P2 are built** in PR #252 (branch `nbleicher/clean-data-db3-p0`, commits P0 `0eaf3f4`, P1 `97237e0`, P2 `5598a57`). It is not merged and not released.
  - D15 is built as assumed. The switch is `PROMOTE_DB2_ONLY_LEADS_ON_CLEAN_EXPIRY = False` in `jawnix/clean_inventory.py`.
  - 0057 also widens the `inventory_snapshots` check constraint to allow `db3`. The plan's migration list had missed this.
- **PR #252 has three problems to fix before merge** (found 2026-11-20):
  - **CI backend fails.** In `tests/test_migration_bootstrap.py`, migration 0057 creates `ck_lead_upload_inventory_database`, and that constraint already exists on a fresh database. The 0055 baseline builds the schema from the current models, so every step in 0057 must check first, the way 0056 does. Only column adds are checked today. Constraints, primary key swaps and indexes are not.
  - **CI e2e fails.** 36 tests fail across inventory, lead lookup, DB2 uploads, Today, shell and accessibility, on both mobile and desktop. The e2e mocks (`frontend/e2e/inventory-fixtures.ts` and the other fixtures) were never given the new `db3` and `cleanExpiringSoon` fields, so the Inventory pages don't render. On main, both jobs pass at `c74bd6d`.
  - **D6 and T9 are not as planned.** A DB3 upload uses one form date (`lead_uploads.scrubbed_on`) for every row and ignores the per-row `Scrubbed On` column. The build's T9 tests per-database availability snapshots instead. It said the plan only had T1–T8, but the plan it committed has T9.
- **P3 is not started.**
- **Code references** are checked against `origin/main` of `nbleicher/jawnix` at `c74bd6d` (#251). The newest migration there is `20261112_0056`.

## Goal

Sell prescrubbed "Clean" leads at $5 per 1,000, in any quantity, from a new database, DB3. DB1 and DB2 keep working exactly as they do today and show no scrub information. Standard buyers scrub on their own; Clean buyers pay the premium for leads that are already scrubbed.

## Decisions

- **D16 Where requests draw from.** On each request the customer picks Standard or Clean. Clean always draws from DB3. Standard draws from the customer's `lead_database` (`db1` or `db2`), as it does today. There are no database pairs and no DB4.
- **D4 Price.**
  - Clean uses one platform rate, `JAWNIX_CLEAN_LEAD_RATE_CENTS_PER_THOUSAND=500`. Clean is off while the setting is unset, which makes it the launch switch.
  - Standard keeps each customer's own rate.
  - Both rates are frozen on the request at submission, as they are today (`billing.py:prepare_submission`).
  - Every customer pays: all 17 active customers had billing on as of 2026-11-19. A Clean request from a customer without billing is refused.
- **D6 Freshness.**
  - Each DB3 row carries its own scrub date. The upload reads a `Scrubbed On` column per row, which the match script always writes. The upload form's date is used only when that column is missing.
  - Rows dated in the future or more than 30 days ago are rejected into the upload's error report.
  - A row is sellable for 30 days after its scrub date. A newer upload of the same phone resets the date.
- **D9 Upload target.** An upload goes to DB2 or DB3. DB3 rows are stored in the existing upload store (the physical `jawnix_db2` database), as DB2 rows are. Nothing new needs provisioning or backing up.
- **D10 Memberships.** There is one membership per phone per database, so a phone can be in DB2 and DB3 at once.
- **D11 Sales rules.** The existing sales filter (`allocation.py:eligible_query`) is unchanged apart from choosing the database. Suppression, holds, exclusions, niche policy, cooldown and permanent no-repeat all apply to DB3.
- **D14 No holding back.** DB1 and DB2 keep selling phones that are also fresh in DB3.
- **D15 Expiry.**
  - A nightly job deletes expired DB3 memberships, and their scrub dates go with them.
  - A phone that is in neither DB1 nor DB2 is added to DB1 as a plain lead: `in_db1 = true`, with title and state copied from its DB3 upload row. Inventory sync already does the same thing when the scraper finds a DB2 phone (`jawnix_data/migration.py:637-642`).
  - A phone already in DB1 or DB2 only loses its DB3 membership. This part is assumed, not confirmed: the user said "DB3 expired leads move to DB1". Confirm whether phones that are only in DB2 should also be added to DB1.
  - Two things are kept: the upload's own entry in the upload list (file, date, counts), and the delivery records of past DB3 sales. The sales rules and billing depend on those delivery records.
- **D17 No scrub information in DB1 or DB2.** Their pages, counts, batch CSVs and lead records stay exactly as they are. Scrub dates appear only on DB3 uploads and DB3 memberships.

## How repeat prevention works (unchanged; DB3 inherits it)

Each phone has one lead record (`lead_inventory`), shared by DB1, DB2 and DB3 (ADR 0002). Every delivered phone writes a `distribution_events` row with the customer, agency, time and database. When a request picks leads, it excludes any phone that breaks these rules:

1. **Never twice to the same buyer** (`allocation.py:113`, `_same_recipient_clause`). A phone is skipped if it was ever delivered to the same customer, the same agency or any customer in it, or a restored identity of the same customer (`permanent_history_key`). This covers every database and never expires.
2. **Cooldown for everyone else** (`allocation.py:168`, `cooling_down`).
   - A phone is skipped if it was delivered to any active customer within the requesting customer's `cooldown_window_days`. That was 7 days for every customer on 2026-11-19.
   - A delivery to a deactivated customer blocks the phone for 3 days (`DEACTIVATED_RECIPIENT_HOLD_DAYS`).
   - A deactivated customer's history doesn't count for rule 1 until they are reactivated.
3. **No double pick.** Picked rows are locked with `FOR UPDATE SKIP LOCKED`, and the distribution is committed at allocation (ADR 0003). Two requests running at the same time can't take the same phone.

The rules read history by phone, not by database. So:
- A DB1 sale blocks the phone in DB3, and a DB3 sale blocks it in DB1 and DB2.
- Moving a phone from DB3 to DB1, or uploading it again, doesn't reset its history.

## Edge cases

- **E1** A Clean request larger than the DB3 stock goes to `waiting_inventory`, as Standard requests do. It fills once new uploads are published (`fulfill_round_robin`).
- **E4** Rows that expire or get suppressed while a request waits are skipped. The filter runs at the moment leads are picked.
- **E5** Customer pages keep supply hidden (`CustomerNewRequest.tsx`: "Supply stays hidden"). They show Clean's rate and cost, but never Clean counts.

## Scrub workflow (operator)

1. The operator scrubs their own lists in Textro: about 10M+ first, then about 1M at a time as purchased data arrives. Clean means Textro's "scrubbed-mobile": no blocking flag (litigator, TCPA troll, complainer, federal DNC, state DNC) and a mobile line that isn't VoIP.
   - Map the title to `company` and the state to `state`.
   - Keep each file under 32 MiB.
   - Run one import at a time, off-hours.
   - Download each import's clean file (`import_clean`). The clean file is the uploaded CSV's own columns, verbatim. It has no scrub date and no normalized phone, so note each import's date.
   - Textro lives at `/Users/noahbleicher/textro`. Read it only; never edit it.
2. Run `ops/match-clean-export.py` (built in P0) in the api container. It only reads the database:
   ```
   docker compose run --rm -v /path/to/exports:/exports api python /app/ops/match-clean-export.py \
       /exports/clean.csv --output-dir /exports/matched --scrubbed-on <Textro import date>
   ```
   - **Title and state.** It uses DB1 first (`Lead.title`/`Lead.state`). If a Lead Correction exists, the corrected `Lead` values win. Next comes the DB2 upload row, then the export's Company and State. An empty title falls through to the export's Company. A full state name (for example "Vermont") is converted to its code.
   - **Scrub date.** It takes the date from a `Scrubbed On` column in the export if there is one. Otherwise `--scrubbed-on` is required.
   - **Output.** One `clean-<STATE>.csv` per state with `Phone, Company, State, Scrubbed On`. A `rejects.csv` lists:
     - repeated phones (the first one is kept)
     - numbers that won't normalize
     - rows with no usable state
     - bad dates
   - It prints a JSON summary of the counts.
3. Upload each file to DB3. Optionally, a command-line tool can queue all the files through the normal upload pipeline. The parser already reads the Phone and Company headers (`lead_uploads.py: PHONE_HEADERS, TITLE_HEADERS`).
4. Re-scrub the first batch right after day 30. Before then, Textro's 30-day Scrub History cache may return the old result.

Do not build DB3 from an export of Textro's Scrub History. It is Customer Personal Information under Textro's DPA (§5.1, Annex I(B)) and its privacy policy.

## P0 result (2026-11-20)

- **Script.** `ops/match-clean-export.py` (216 lines) is on branch `nbleicher/clean-data-db3-p0`, uncommitted. It lives in the clone at `/Users/noahbleicher/conductor/workspaces/jawnix-v2/stockholm/jawnix`. That clone is a separate clone of `nbleicher/jawnix`, not a worktree of `~/Projects/jawnix-v2/jawnix`.
- **Pilot (Vermont, 1,510 rows).** It used 1,000 production DB1 leads seeded into a local DB1, 500 uploaded through the real DB2 pipeline, one correction, and 10 synthetic new purchases.
  - All rows went through a local Textro import with `SCRUB_ADAPTER=fake`, where every number comes back clean-mobile.
  - The match result was 1,510 matched and 0 rejected. All four title and state paths were checked: DB1, correction, DB2 upload row, and the export fallback.
  - Uploading the result through `import_lead_upload` gave 1,510 valid rows and 0 rejected. The upload went into local DB2, because DB3 doesn't exist yet.
- **Edge cases checked:** repeated phones, a bad number, a missing state, a carried `Scrubbed On` column, a full state name, and a file with no Phone column.
- **Not tested:** real Blacklist Alliance results. The pilot's local Textro copy used its test scrub mode, which marks every number clean. That affects only how many numbers come back clean, not any Jawnix code.
- **P0b (optional, before P3):** run one small real import in your normal Textro account on app.textro.ai to see the real clean share. No Jawnix work is involved.
- **Script changes needed in P1:**
  - after migration 0057, read only `db2` memberships (it currently reads any membership by `lead_id`)
  - import the renamed `upload_listings` instead of `db2_listings`
- **Cleanup when done:**
  - `stockholm/.context/` contains 1,500 production lead rows (`vt-sample.csv` and the pilot files). Delete them.
  - A test user `jawnix-pilot@example.com` was created in Textro's Clerk development instance.
  - The local `textro_pilot` database exists in `textro-postgres-1`.
  - The local Jawnix volume `jawnix_postgres_data` and the clone's `.env` exist. The `.env` is gitignored.

## Migration 0057

- **`lead_uploads`:**
  - add `inventory_database varchar(4)`: `db2` or `db3`, default `db2`
  - add `scrubbed_on date`: the form fallback for DB3, empty for `db2`
- **`lead_database_memberships`:**
  - add `inventory_database`, with existing rows set to `db2`
  - add `expires_on date`, empty for `db2`
  - change the primary key from `lead_id` to `(inventory_database, lead_id)`
  - add an index on `(inventory_database, state)`, plus one on `expires_on` for `db3`
- **`lead_requests`:** add `clean boolean not null default false`.
- **`customer_availability_snapshots`:** key it by `(customer_id, inventory_database)`.
- **No change needed:**
  - `agents.lead_database`: `ck_customer_lead_database` still allows only `db1` and `db2`.
  - `distribution_events.inventory_database`: it already exists with no check constraint, so it can take `db3`.

## Code changes

1. **Sales path.**
   - `lead_databases.py`:
     - `inventory_database(request)` returns `db3` when `request.clean`, otherwise `customer.lead_database`.
     - `inventory_scope` filters memberships by database, and for `db3` also by `expires_on > today`.
     - `db2_listings` becomes `upload_listings` and records the database in provenance.
   - `allocation.py`:
     - lines 214 and 634 use the new `inventory_database`
     - the `== "db2"` checks at 659–683 become "any upload-backed database"
2. **Uploads.**
   - `lead_upload_api.py`: the form takes the database and an optional scrub date, and returns DB2 and DB3 totals.
   - The upload parser reads `Scrubbed On` per row for DB3.
   - `lead_uploads.py:_publish`:
     - upserts memberships per database with `expires_on`
     - updates that database's `lead_database_summaries` row
     - clears the affected availability snapshots
     - adds the database to the Activity details
3. **Billing.**
   - `billing.py:prepare_submission` (line 312) gets `clean=`. Clean requests use the Clean rate. It refuses when Clean is off or the customer isn't billed, and compares the expected rate with whichever rate applies.
   - `config.py` gets the new setting, allowed between 100 and 2000.
   - `customer_billing.py` exposes the Clean rate.
   - `customer_requests.py` and `schemas.py` accept `clean`.
4. **Nightly job `expire_clean_inventory` (D15).**
   - Moves phones that are only in DB3 into DB1.
   - Deletes expired DB3 memberships.
   - Queues the inventory refresh.
   - Writes one Activity entry per run with counts.
5. **Admin reads.** These add DB3 and its expiring-soon count. Their DB1 and DB2 output is unchanged (D17).
   - `inventory_snapshots.py` (`DATABASES`)
   - `inventory.py`
   - `operator_numbers.py`
   - `pool_analytics.py` (Standard and Clean availability)
   - `eligibility.py`
   - `lead_lookup.py`
   - `search.py`
   - `batches.py:383`
6. **Frontend.**
   - **Customer:**
     - `CustomerNewRequest.tsx` and `batchRequests.ts` get a Standard or Clean choice. The rate, cost and expected rate follow the choice, and the choice is hidden while Clean is off.
     - The Batches list shows "Clean".
   - **Admin:**
     - `AdminInventoryUploads.tsx` and `leadUploads.ts`: database and scrub-date fields
     - `AdminInventory.tsx`: DB3 by state, and rows expiring within 7 days
     - `AdminCustomerAvailability.tsx`: Standard and Clean
     - DB3 labels in `AdminBatches.tsx`, `format.ts`, `AdminLeadLookup.tsx`, `GlobalSearch.tsx` and `OperatorNumbers.tsx`
7. **Docs.**
   - ADR 0022: Clean is priced separately from the customer's Lead Rate. This changes ADR 0018's amended rule, "a Billed Customer pays their own Lead Rate for every batch from either database", for Clean only.
   - `CONTEXT.md`: update the terms Lead Database, Lead Upload, Usable Inventory and Lead Rate, and add Clean.

## Tests (Postgres)

Use `tests/test_postgres_lead_databases.py` as the model. These tests run against `JAWNIX_ACCEPTANCE_DATABASE_URL`.

- **T1** A DB3 upload leaves the same phone's DB1 or DB2 status unchanged.
- **T2** Expired DB3 rows are never picked.
- **T3** A Clean request picks only DB3 rows and freezes 500 cents per 1,000.
- **T4** A customer who bought a phone from DB1 or DB2 never gets it from DB3. Cooldown blocks the phone across all three databases.
- **T5** A Clean request is refused when Clean is off, when the customer is unbilled, or when the expected rate doesn't match.
- **T6** A Clean request that is short of stock waits, then fills after a DB3 upload is published.
- **T7** Expiry:
  - a phone that was only in DB3 is in DB1 the next day, with no DB3 membership left
  - a phone already in DB1 or DB2 only loses its DB3 membership
  - DB1 batch CSVs keep today's columns
- **T8** A phone moved into DB1 doesn't reset rules 1 and 2.
- **T9** A DB3 upload uses each row's `Scrubbed On` and rejects rows with a future date or one more than 30 days old.

## Local development (learned in P0)

- **Jawnix local stack.** In the clone, use a local `.env`, then run:
  1. `docker compose build api`
  2. `docker compose up -d postgres`
  3. `docker compose run --rm migrate`
  4. `docker compose run --rm api python -m jawnix.db2_setup`
- **Tests.** pytest is not in the api image. CI uses `uv sync --python 3.12 --extra test` and then `uv run pytest`.
- **Test data for P1–P3.** No Textro is needed. Feed the match script or the DB3 upload a hand-made CSV with Phone, Company, State and Scrubbed On columns.

## Rollout

1. **P0: done.** P0b, a live-vendor check, is optional.
2. **P1+P2:** one release with Clean off (rate unset), using `ops/release-prepare.py` and `ops/release-activate.py`. The procedure is in `docs/OPERATIONS.md`. Production is reached with `ssh jawnix`; the compose project is in `/srv/jawnix/app`.
3. **P3:** upload the first full scrub to DB3 and check the counts on Inventory. Then set `JAWNIX_CLEAN_LEAD_RATE_CENTS_PER_THOUSAND=500` and release again to turn Clean on.

## Separate: Standard price change (Monday 2026-11-23)

Change all 17 customers from 150 to 180 cents per 1,000 in the admin billing controls. Set `JAWNIX_DB1_DEFAULT_LEAD_RATE_CENTS_PER_THOUSAND=180` in production `.env` for the next release. Requests submitted before the change keep 150.

## Risks

- **R1** Buyers are separate sellers. Ask counsel about scrubbing for them under the operator's DNC registry subscription number (SAN). Textro has the same open question.
- **R6** Blacklist Alliance's terms grant a "personal use" licence. Get their OK to resell scrubbed records.
- **R10** Textro answers repeat numbers from results up to 30 days old, so a row's real check can be older than its scrub date.

## Reference numbers (2026-11-19)

- **Inventory:** `lead_inventory` holds 10,303,867 leads: 9,582,107 in DB1 and 1,082,619 in DB2, with 360,859 in both.
- **Customers:** 17 active, 14 on DB1 and 3 on DB2. All are billed at 150, with a cooldown of 7 days.
- **Blacklist Alliance:** Elite Plus plan, $4,000 a month for 15M records ($0.000267 per record). Records past 15M are billed at the same rate. The account is shared with Textro, which had used 8.0M of November's records by Nov 18.
