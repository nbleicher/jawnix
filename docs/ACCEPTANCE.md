# Production acceptance record

## 2026-07-26 completion verification

Issue #27 repository verification passed against a disposable PostgreSQL
instance upgraded through the full Alembic chain:

- 56 local tests passed; the environment-gated PostgreSQL acceptance test is
  the only normal skip in the local run.
- The real-PostgreSQL flow covered versioned Google Maps publication, a
  separate Inventory Sync transaction, delivery failure/retry, Customer
  outcomes and reports, reversible suppression and correction, artifact
  regeneration, anomaly and Inventory Conflict decisions, Nightly Review,
  recommendation approval and denial, version-aware restore validation,
  crash-safe Nightly Review delivery reconciliation, and concurrent User
  Account replacement, Inventory Sync visibility, agency-first Fulfillment
  Rotation, Inventory Conflict decisions/allocation, same-request allocation,
  and configuration activation. The scheduler, Telegram webhook, durable job
  claim, and worker seams are exercised directly. Inventory Conflict, Scrape
  Anomaly, and Source Recommendation records are generated through their
  production services rather than inserted as acceptance shortcuts. Boundary
  tests cover new segments, zero-result runs, large increases, duplicate
  decisions, and superseding stale anomalous output after a newer run. A
  dataset-wide PostgreSQL transaction lock also serializes publication,
  scheduled activation, cross-configuration anomaly decisions, restore
  application, and committed-dataset reads by manual Inventory Sync. The
  `sync-scrapers` command never launches acquisition against the active file;
  it resolves the latest committed Dataset Publication and records an
  Inventory Sync Attempt for that exact version.
- Downgrade to revision `20260725_0007` and upgrade through
  `20260726_0020` passed.
- The public OpenAPI document contains only Customer/User Account
  terminology; legacy Agent/Recipient names remain hidden persistence and
  route compatibility details.

This is repository acceptance, not a new production acceptance. Before
deployment, repeat migration reconciliation with an authorized sanitized
production-size PostgreSQL snapshot and realistic Google Maps Scraper Dataset,
exercise both older-rejection and newer-replay restore paths, verify a
both-store Restic restore, and record staging/live smoke evidence here. No such
snapshot or backup repository is stored in this workspace.

## 2026-07-25 cutover record

Validated on 2026-07-25 against `https://jawnix.com`. DNS cut over to the VPS
at approximately 03:33 UTC. Railway, the legacy Supabase project, the protected
Git rollback tag/bundle, and the read-only `dat` source remain available.

## Passed

- The protected Git bundle, annotated rollback tag, encrypted Railway export,
  encrypted Supabase application-data export, and source checksum manifest
  reverified. The bundle records a complete Git history.
- The attached `dat` directory was used only as migration input. Its manifest,
  SQLite database, CSVs, history, configuration, and scripts were not edited,
  moved, or deleted.
- Python compilation, Ruff, dependency checks, and 24 automated tests passed.
  Docker images build with Python 3.12 and PostgreSQL 18 client tools.
- Caddy, FastAPI, PostgreSQL 18, the worker, scheduler, and backup worker are
  running on the VPS. Production and staging readiness pass over HTTPS,
  billing is disabled, and PostgreSQL is private to the Docker network.
- `jawnix.com` and `www.jawnix.com` resolve directly to `159.195.15.51` with
  five-minute TTLs. Caddy obtained valid Let's Encrypt certificates. The exact
  prior Railway and Porkbun DNS records are saved outside Git for rollback.
- The new Supabase Auth project is in `us-east-1`. It has 17 Auth identities
  and 16 customer profiles, including nine legacy customer identities.
  `noah@jawnix.com` is the dedicated admin and has no customer profile;
  `noah@urpriorityhealth.com` remains the confirmed `noah`/Summit customer.
- All eight approved customer mappings are confirmed against Summit:
  `noah`, `jack`, `jo`, `max`, `tim`, `spencer`, `matthew`, and `ali`.
- The admin portal can synchronize users, confirm mappings, create customers,
  and send password setup/reset emails. Supabase Auth uses Resend custom SMTP
  from `hai@jawnix.com` and permits production and staging redirect URLs.
- The Recipients page includes an expandable agency → agent → customer
  hierarchy. Agency names/statuses and agent names/memberships/statuses are
  editable; immutable slugs preserve distribution-history identifiers.
  Typed-slug deletion removes agents or whole agencies from the active
  hierarchy, unassigns affected customers, and retains tombstone records so
  requests, allocation history, and permanent no-repeat enforcement survive.
- Telegram points to the production webhook with zero pending updates or
  errors. Resend delivery/failure webhooks point to production; a password
  reset produced a verified Resend receipt in PostgreSQL.
- The final legacy Supabase delta contained nine profiles and zero requests.
  Source checksums matched the pinned import exactly before cutover.
- The current production database reconciles to:

  | Destination | Rows |
  |---|---:|
  | Unique inventory phones | 9,244,326 |
  | Source provenance | 9,541,530 |
  | Distribution events | 4,588,286 |
  | Customer profiles | 16 |
  | Requests | 1 delivered smoke request |
  | Confirmed customer mappings | 8 |
  | Migration audits | 3 |
  | Quarantined rows | 143,037 |
  | Failed jobs | 0 |

- The first live scheduled collector downloaded
  `NPPES_Data_Dissemination_July_2026_V2.zip`, SHA-256
  `82b43e03504550112bd375d66c3498a259dbaa2172824d57dc3f3241e9994adf`.
  It atomically refreshed 7,369,238 NPPES rows, added 664,970 unique inventory
  phones, and quarantined 27,162 unusable rows. The resulting SQLite checksum
  is `7bba3b4f8b0aab3acc3e035aa5bfd36185180524e11d4c3c6a206541ab3ba55a`.
- Two concurrent 50-row allocations produced 100 unique distribution events
  without overlap. A rollback-only 100,000-row allocation and CSV generation
  completed in 80.14 seconds against production-size inventory.
- The production smoke request
  `19f4867b-ce7d-47d0-80e2-3dc45e29198f` completed:
  Telegram notification, approval, one unique TX allocation, exact
  `phone,title` CSV, and Resend delivery. The two-line artifact SHA-256 is
  `dc737760070d2352e51543cc04cb089b88864466c69532679d28d99fb400e1c0`.
  Noah's saved states were restored to their pre-test empty value afterward.
- The VPS Restic repository passed integrity checking across all 14 snapshots
  after the production
  import. Current database snapshots include `05c6d273` and the post-smoke
  snapshot `ba2a7468`; current WAL snapshot `f8f4f36d` covers the cutover.
- The encrypted Peely SSD repository copied all 14 corresponding snapshots,
  including destination snapshot `55b59a90` for the post-smoke database.
  Its independent full repository check passed with the 14-day retention
  policy applied.
- VPS root, application session, PostgreSQL, Telegram webhook, and VPS Restic
  credentials were rotated after cutover and stored in macOS Keychain.
- `jawnix-cutover-monitor.timer` checks both health endpoints and all six
  services every five minutes. It records to journald and sends Telegram
  alerts on healthy/unhealthy transitions.

## Remaining operational actions

- Rotate provider-managed credentials that cannot be safely replaced from the
  application: the Cloudflare API token, Supabase management/legacy JWT keys,
  Telegram bot token through BotFather, and Resend API key/webhook secret.
  Update the VPS/Keychain and reverify each integration immediately afterward.
- Use the admin Recipients page to send password setup/reset emails when each
  customer should be activated. Do not send them in bulk without review.
- Keep Railway and legacy Supabase data live through at least
  2026-07-27 03:33 UTC. Review the five-minute monitor, application/worker
  errors, failed jobs, Telegram, Resend failures, PostgreSQL storage, and
  backups throughout that window.
- Do not scale down Railway or delete any rollback material without explicit
  approval after the observation window.
