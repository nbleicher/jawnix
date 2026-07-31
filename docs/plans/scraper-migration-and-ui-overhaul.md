# Delivery Plan — Finish the UI Overhaul and Migrate the Scraper Service

One document, every step in execution order, one ready-to-paste prompt per step.
Written 2026-07-31. Tracking: UI cutover #71/#119, portal overhaul #116, parent
epic #46; the Scraper migration ADR lands as 0017 in step S1's wake.

## How to use this document

- Each step is sized for one Conductor workspace and one PR (operator steps
  excepted). Paste the prompt into a fresh workspace — prompts are
  self-contained and assume no shared context.
- `<S1-ref>`-style placeholders mean "the merged PR number or branch of that
  step"; fill them in when pasting.
- Steps marked **[operator]** change production state — run them with a human
  present; their prompts instruct the agent to pause for confirmation before
  every state-changing action.
- Every code step lands on `main` through a reviewed PR. Both hosts deploy only
  from tagged `main` revisions, and (after step 0.2) only through
  `ops/deploy.sh`.
- Playwright visual baselines are Linux/CI-generated: regenerate from CI, never
  locally. Backend tests are hermetic (`tests/conftest.py` clears the settings
  `env_file` at import time — never build clients from settings at module
  import).

## Delivery order

| Order | Step | Track | Depends on | Status |
|---|---|---|---|---|
| 1 | 0.1 Operator smoke walk (#119) | UI Phase 0 | — | ✅ done 2026-07-31 |
| 2 | 0.2 Guarded deploy script (rsync excludes) | Ops | — | |
| 3 | 0.3 Stabilization sign-off, close #71 | UI Phase 0 | 0.1 + 48 h monitoring | |
| 4 ∥ | S1–S5 Scraper Stage A (typed control plane) | Scraper | dev: none; deploy: 0.2 | |
| 4 ∥ | P1–P7 Portal overhaul slices (#116) | UI Phase 1 | 0.1 passed; deploy: 0.2 | |
| 5 | S6–S7 Scraper Stage B (generation + history) | Scraper | S1–S5 | |
| 6 | S8–S9 Scraper Stage C (rehearsal + cutover) | Scraper | S6–S7 | |
| 7 | P8 Static-page retirement | UI Phase 0 close-out | 0.3 + explicit approval | |
| 8 | S10 Scale remnant removal | Scraper | S9 + 7-day rollback window | |
| later | X1 Admin-phase overhaul spec | UI Phase 2 | #116 shipped | |

Steps marked ∥ run in parallel workspaces; the two tracks are code-disjoint
(backend/acquisition vs. `frontend/` customer portal), so only production
deploys serialize. Within each track the steps are sequential. P8 and S10 are
independent of each other — each lands when its own window closes.

---

## Phase 0 — finish the UI cutover (do first)

The #71 flag flip already happened in production (2026-07-31, PR #117, fix
#118). What remains gates everything else: the smoke walk validates the flip,
the deploy script unblocks all future full deploys, and sign-off closes the
stabilization window.

### Step 0.1 — Operator smoke walk **[operator]** — ✅ complete 2026-07-31

All #119 checklist items A1–A4 and B5–B10 are ticked. Remaining on #119 are
the 48-hour monitoring window and the sign-off decisions, which step 0.3
closes out. Original prompt retained for the record:

```
Production smoke-walk support for the UI cutover (issue #119, checklist items A and B).
Without changing any production state: verify from outside that jawnix.com serves the React
shell (deep-route hard refresh returns 200 with no-store on the shell document and immutable
cache headers on hashed assets), that all eight legacy URLs still 302 to their /app routes
preserving query strings, that /health endpoints answer, and that the jawnix-cutover-monitor
timer is reported active per docs/OPERATIONS.md. Then print the remaining manual checklist
items A1–A4 and B5–B10 from issue #119 verbatim as a walk sheet, with the pass criteria for
each. As I confirm each item in this conversation, check it off on issue #119 with
`gh issue comment` / checkbox edits. Do not perform the customer journey yourself — item B
writes real Distribution Events and must use the designated test customer, driven by me.
```

**Done when:** every A and B checkbox on #119 is ticked, or failures are
recorded as new issues.

### Step 0.2 — Guarded deploy script with pinned rsync excludes

Blocking for all later production deploys. The last full-deploy dry-run showed
rsync `--delete` would destroy the host-owned `batches/` directory (#119); the
previous deploy was a hand-picked five-file sync, and no deploy script exists.

```
Create a guarded deploy script for the Jawnix application host and document it. Context: the
host at /srv/jawnix/app has no git; deploys are rsync from a checkout. The last full-deploy
dry-run showed rsync --delete would destroy the host-owned batches/ directory, so the previous
deploy was a hand-picked five-file sync (see issue #119). Write ops/deploy.sh that: takes a
tagged main revision as its argument and refuses to run from a dirty or untagged checkout;
always runs rsync --dry-run first and shows the resulting change/delete list for explicit
confirmation before the real sync; pins an exclude list covering at minimum batches/, the
production .env, and any other host-owned runtime data directories you find referenced in
docker-compose.yml, docs/OPERATIONS.md, and ops/backup.sh; and never passes --delete without
those excludes. Add a "Deploying the application host" section to docs/OPERATIONS.md that makes
this script the only sanctioned deploy path. Match the style of the existing ops/*.sh scripts.
Do not run it against production. Open a PR to main.
```

**Done when:** `ops/deploy.sh` is merged and OPERATIONS.md names it the only
sanctioned deploy path.

### Step 0.3 — Stabilization sign-off **[operator]**

```
Close out the UI cutover stabilization (issue #71). The 48-hour monitoring window after the
2026-07-31 flag flip has elapsed. Review the cutover-monitoring evidence per the "UI cutover
(#71)" runbook in docs/OPERATIONS.md and the #119 checklist state. If every #71 acceptance
criterion is met (treat the one-time User Account migration criterion as N/A per the note
recorded on #70/#71 — invitations are manual), write a closing summary on #71 recording: flip
date, deployed revisions, smoke-walk results, monitoring outcome, and the standing rule that
static-page retirement remains a separate explicitly approved change. Close #71 and update
#119. If any criterion is not met, report exactly which and stop without closing anything.
```

**Done when:** #71 is closed with the summary, or the unmet criteria are
reported.

---

## Scraper track — Stage A: typed control plane replaces HTML scraping (S1–S5)

Today every `/api/admin/scraper/...` endpoint proxies to Scale and scrapes
HTML (~1,900 lines of `HTMLParser` across four modules); `tests/scraper_fake.py`
(1,066 lines) fakes Scale in HTML. Stage A replaces all of it with a typed JSON
control plane while keeping every browser contract byte-identical.

### Step S1 — Import legacy Scraper control source and vendor the Go worker

```
In the Jawnix repo, create the scraper/ subsystem by importing the legacy Scale service as a
provenance-recorded snapshot (no git-history merge). Source: https://github.com/nbleicher/scale
at release tag scraper-source-baseline-2026-07-27-v4; a local checkout carrying that tag exists
at /Users/noahbleicher/conductor/archived-contexts/jawnix/dos/scale-readonly. (Per Scale's
SCRAPER_BASELINE.md, the "Conductor Playground" React project only supplied Terminal CLI design
language — it is not a source.) Import the control logic, database migrations, deployment
configuration, monitoring, exports, pipeline controls, rollover tasks, and scheduled
operations. Vendor the Go worker source from https://github.com/nbleicher/scraper at the
immutable revision 7caca2ce8122c0ffaf47ca5737a06d05a23a90ca, applying Scale's tracked
build-only resource controls from scraper_changes/worker-build.patch, keeping the worker's MIT
license file, and recording provenance (source repos, tag/commit, patch, import date) in
scraper/PROVENANCE.md; exclude the generated binary and results.csv. Do not wire anything into
the running Jawnix app yet — this step only lands the source plus CI. Add a Go job to
.github/workflows/ci.yml that builds the vendored worker and runs its tests, alongside the
existing Node and Python jobs. Ensure the Python import does not break the existing pytest run
(keep it outside the jawnix package or guard it from collection). Also write ADR 0017
recording Jawnix ownership of the Scraper system, the two-host topology, and the retirement of
Scale as a production source (ADR numbering note: 0016 is taken; two ADRs previously shared
0011 and one was renumbered). Open a PR to main.
```

**Done when:** `scraper/` + vendored worker + PROVENANCE.md + ADR 0017 are
merged and the Go CI job is green.

### Step S2 — Build the headless `scraper-control` service

```
Build the headless scraper-control process in the scraper/ subsystem imported by the previous
step (branch/PR: <S1-ref>). It replaces the Scale HTMX dashboard with typed JSON operations
served over the WireGuard interface on the acquisition host, covering every operation the
Jawnix proxy consumes today (enumerate them from jawnix/scraper_proxy.py): keywords
list/preview/save/generate-support/rollover, winners, database workspace/states/exports/
regeneration, coverage states/keywords/cells, monitoring dashboard, pipeline controls, runtime
configuration preview/save, campaign history, workspace summary. Absorb the JSON endpoints
Jawnix already uses — /api/dashboard* (currently added to Scale by
docs/scale-dashboard-json-api.patch; delete that patch file once absorbed) and
/api/source-segments*. Authenticate every request with a bearer token from
JAWNIX_SCRAPER_CONTROL_TOKEN; never log the token. Define the response models in one shared,
typed contract module, and write contract tests that exercise every endpoint against the
service with a seeded database. Generation stays upstream for now — expose the existing
generation behavior as a typed operation without changing its semantics. Open a PR to main.
```

**Done when:** every proxy-consumed operation has a typed endpoint with a
passing contract test, and the dashboard JSON patch file is gone.

### Step S3 — `ScraperOperations` interface; migrate keywords off HTML

```
In the Jawnix backend, introduce a ScraperOperations interface: a production HTTP adapter
speaking the typed scraper-control JSON contract (token from JAWNIX_SCRAPER_CONTROL_TOKEN, base
URL from the existing scraper-ops settings) and an in-memory fake for tests. Then migrate the
keywords domain onto it: jawnix/scraper_proxy.py keyword endpoints (list, preview, save,
generate, rollover, winners) stop fetching HTML and call ScraperOperations; delete the HTML
parsers in jawnix/scraper_keywords.py (parse_editor, parse_winners, parse_rollover,
parse_generation_draft, parse_feedback_error and the _TreeParser machinery), keeping the typed
request/response models. Hard constraints: every /api/admin/scraper/... request and response
stays byte-compatible — same JSON shapes, same user-facing error strings, same 409/422/503
mapping — so frontend/src is untouched and its tests pass unmodified. Begin rewriting
tests/scraper_fake.py: replace its HTML keyword pages with the in-memory ScraperOperations
fake while other domains keep the HTML transport (the fake may serve both during this
transition). Update tests/test_scraper_keywords.py and any other suites touching keywords.
Keep tests hermetic per tests/conftest.py (env_file is cleared at import time — do not build
clients from settings at module import). Run the full backend and frontend suites. Open a PR
to main.
```

### Step S4 — Migrate database, exports, and coverage off HTML

```
Continue the Jawnix scraper typed-contract migration (after <S3-ref>). Move the database
browsing/exports and coverage domains off HTML onto the ScraperOperations interface: the
/api/admin/scraper/database*, /database/exports*, /coverage* endpoints in
jawnix/scraper_proxy.py call typed operations; delete the HTML parsers and _MarkupTree/
_FragmentTree machinery in jawnix/scraper_database.py and jawnix/scraper_coverage.py, keeping
typed models. Extend the in-memory ScraperOperations fake in tests/scraper_fake.py to cover
these domains and delete their HTML fixtures. Byte-compatibility rules are identical to the
keywords step: same JSON shapes, error strings, and status codes; frontend untouched; existing
frontend tests pass unmodified. Update tests/test_scraper_database.py and
tests/test_scraper_coverage.py. Run the full backend and frontend suites. Open a PR to main.
```

### Step S5 — Migrate runtime, workspace, pipeline, history; delete the last parsers

```
Finish Stage A of the Jawnix scraper typed-contract migration (after <S4-ref>). Migrate the
remaining domains — runtime configuration (preview/save), workspace, pipeline controls,
monitoring, and campaign history — onto ScraperOperations; delete jawnix/scraper_runtime.py's
_ScaleHTML and every remaining HTML parser. Completion criteria: no html.parser/HTMLParser
import remains anywhere under jawnix/ (add a test asserting this); tests/scraper_fake.py is
now purely the in-memory ScraperOperations fake with zero HTML fixtures; all nine backend
scraper test suites pass; every /api/admin/scraper/... contract is byte-identical (shapes,
error strings, status codes); frontend code and tests unmodified. If any Playwright visual
baseline changes, regenerate it from CI, not locally. Run the complete backend, frontend, and
browser suites. Open a PR to main.
```

**Stage A done when:** zero HTML parsing under `jawnix/`, the fake is fully
in-memory, and the frontend is untouched with all suites green.

---

## Portal track — Phase 1: customer-portal slices, #116 (P1–P7)

Runs in parallel with Stage A after step 0.1 passes. Slices ship live and
incrementally; the theme lands first so every later slice is built under
Opaline. Acceptance per slice is defined in #116; all existing axe/WCAG 2.2 AA,
keyboard, and visual-regression gates keep running throughout.

### Step P1 — Opaline theme, gallery coverage, display typeface

```
Implement the Opaline visual identity for the Jawnix React shell per issue #116 (visual
decisions section). Add a data-theme="opaline" theme inside the existing token contract: dark
ember/indigo surfaces derived from #12060d / #03040c, film-cyan primary accent #3fd2ff
adjusted to meet WCAG AA on its surfaces, amber for warnings/attention, and the
amber→magenta→cyan→deep-blue ramp reserved for status and special moments. Extend the
design-system gallery to render every component under the new theme, and keep all existing
axe/WCAG 2.2 AA, keyboard, and visual-regression gates passing. For the display typeface:
produce three mockups (sign-in, customer Overview, a data-dense admin table) with three
candidate typefaces for headings/numerals, present them for my choice before wiring the final
one in, and tighten density on data-bearing screens with the chosen face. Playwright visual
baselines are regenerated from CI, never locally. Open a PR to main.
```

### Step P2 — Requests as the single lifecycle screen

```
Rebuild the customer Requests destination in the Jawnix React shell per issue #116: Requests is
the single request-to-delivery lifecycle screen (there is no separate Batches destination;
artifact:request is 1:1). Give each Batch Request a real detail page — deep links like
?request=42 must resolve (today they do nothing) — carrying the milestone graph and, once
delivered, a placeholder slot for the artifact card (the card itself ships in the next slice).
Add active refresh: poll/revalidate while any request is in a non-terminal state and stop
entirely when all are settled. Preserve the guided Request-a-Batch flow and milestone email
behavior unchanged. Keep the axe/WCAG 2.2 AA, keyboard, and visual-regression gates passing
under data-theme="opaline"; regenerate visual baselines from CI. Extend the vitest and
Playwright suites to cover the detail page, deep links, and refresh start/stop. Open a PR to
main.
```

### Step P3 — Portal-primary Batch Artifact delivery (ADR 0015)

```
Implement portal-primary Batch Artifact delivery per ADR 0015
(docs/adr/0015-deliver-batch-artifacts-through-the-portal.md) and issue #116. Backend: add a
customer-facing, authenticated artifact download endpoint that serves a customer's own live
Batch Artifact and nothing else — enforce ownership, respect the 30-day expiry, and audit
downloads; artifact regeneration remains an admin-only audited action. Frontend: on the
Requests detail page (built in the previous slice, <P2-ref>), show the artifact card for
delivered requests — filename, row count, expiry countdown, self-serve Download while live,
and an "expired — contact us" state after 30 days. Demote the delivery email to a notification
pointing at the portal (keep sending it; adjust copy only if it currently claims to be the
delivery channel). Cover the endpoint with backend authorization/expiry tests and the card
with vitest + Playwright coverage. Keep all accessibility and visual gates passing. Open a PR
to main.
```

### Step P4 — Overview as a strictly actionable attention queue

```
Rebuild the customer Overview in the Jawnix React shell as a strictly actionable attention
queue per issue #116. Only items that need the customer appear: batch ready / artifact
expiring soon, request waiting on inventory, feedback nudge, Setup Problems — each deep-links
directly to its action (request detail, artifact download, feedback flow, Account). When
nothing needs the customer, Overview is calm and empty — the hard rule is that nothing
non-actionable appears. Overview remains the landing page and the target of email links.
Remove any current non-actionable content rather than restyling it. Extend vitest and
Playwright coverage for each queue-item type and the empty state; keep axe/keyboard/visual
gates passing under Opaline with baselines regenerated from CI. Open a PR to main.
```

### Step P5 — Scoped in-batch feedback search; distinguishable fetch errors

```
Extend customer Feedback in the Jawnix React shell per issue #116. Add a scoped search within
the customer's own delivered batch — find a lead by partial phone or name without retyping
from the CSV — as a second entry point into the existing confirm → disposition → receipt flow.
Strictly one disposition at a time; the anonymous-failure lookup guard is unchanged; bulk
import is explicitly rejected and must not appear. The search must only ever expose leads from
that customer's own delivered batches — enforce scoping server-side. Also fix the recorded
silent failure: a feedback-history fetch error must render distinguishably from "no feedback
yet". Add backend scoping tests and vitest + Playwright coverage for search, the single-
disposition rule, and the error-vs-empty distinction. Keep all gates passing. Open a PR to
main.
```

### Step P6 — Account: identity, Setup Problems, Licensed States

```
Rebuild the customer Account destination in the Jawnix React shell per issue #116: identity,
setup status, and Licensed States only. Render each Setup Problem by name with what happens
next and who acts — this fixes the current dead end where the Requests blocker points at an
Account page that shows nothing. Keep the existing safe Licensed State management. Add a
persistent mailto support link to the shell chrome. Explicitly out of scope: password center,
notification preferences, contact forms — do not add them. Extend vitest and Playwright
coverage for the Setup Problems rendering and the blocker deep link from Requests; keep
axe/keyboard/visual gates passing. Open a PR to main.
```

### Step P7 — Opaline Three.js scene on sign-in and accept-invitation

```
Add the Opaline Three.js scene to the Jawnix React shell per issue #116, on the sign-in and
accept-invitation screens only — never behind data screens, and the scene code must not load
inside the working app (lazy-load it in those two routes' chunks and verify the main bundle is
unaffected). The scene spec is committed in this repo at docs/opaline-scene-spec.md
("Recreate this Three.js scene: Opaline") — read it in full and follow it exactly, adapting
its standalone-HTML boilerplate (importmap/CDN) to the shell's Vite build: install three as a
dependency at the spec's pinned r143 and lazy-load it in the two routes' chunks. Behavior:
prefers-reduced-motion renders a static frame; WebGL failure falls back to the existing
ember/indigo gradient; the form remains fully usable and AA-contrast-compliant over the scene
in every state. Add Playwright coverage for reduced-motion and the fallback path, plus a
bundle-size assertion that the app chunks exclude Three.js. Visual baselines from CI. Open a
PR to main.
```

**Phase 1 done when:** all seven slices are live behind the existing gates.

---

## Scraper track — Stage B: Jawnix-owned generation and history (S6–S7)

Sequential, after S5. History lands first because generation filters against it.

### Step S6 — Keyword-history storage and the full-history import

```
Add Jawnix-owned keyword-history storage and its import (Stage B of the scraper migration,
after <S5-ref>). In the Jawnix application database (SQLAlchemy models + Alembic migration
following the YYYYMMDD_NNNN_slug convention), add normalized keyword-history records: term
(normalized), first-seen and last-seen timestamps, and origin. Build an idempotent, checksummed
import command (a jawnix_data subcommand, following the existing import-scraper-db pattern)
that imports the union of the legacy enqueue_log, keyword_history, and businesses tables from
a Scraper database; re-running it must be a no-op and it must report row counts and a checksum
for verification against the source. Continuously update history from active lists, winners,
and accepted keyword saves — acquisition-side facts arrive through the typed ScraperOperations
control API only; the acquisition database gains no Jawnix-owned tables (ADR 0001 split).
Tests: import idempotency, checksum verification, normalization (case/whitespace), and
continuous-update paths. Open a PR to main.
```

### Step S7 — Jawnix-owned generation: OpenRouter module, drafts, lock, error contract

```
Move Scraper keyword generation into Jawnix (Stage B, after <S6-ref>). Build a generation
module owning all OpenRouter-backed behavior — broad keywords, adjacent keywords, and the
nightly niche/source-segment proposals in jawnix/nightly.py — behind a provider interface so
prompts, parsing, retries, filtering, and transport are internal. OPENROUTER_API_KEY lives
only on the application host and never reaches browsers or logs. Behavior: filter candidates
against active keywords, winners, imported history, and candidates accepted this run; at most
three adaptive model calls within a whole-operation deadline from a new
JAWNIX_KEYWORD_GENERATION_DEADLINE_SECONDS setting (default 180); feed accepted/rejected
context into retry prompts; return exactly 25 terms or fail with the existing actionable
failure — never pad, never partial — recording attempt/rejection metrics. Persist drafts in
the Jawnix DB (UUID, administrator, mode, seed, model, terms, exclusion/candidate metrics,
timestamps, acceptance status; expire after 24 h, purge after 90 days); generation_id is now
Jawnix-minted; a draft is accepted only after the final keyword save succeeds. Serialize
generation with a PostgreSQL advisory lock following the jawnix_data/scraper.py namespace
convention (non-PostgreSQL no-op); concurrent requests get the same retryable 409 as today.
Contract: POST /api/admin/scraper/keywords/generate keeps its exact draft response shape, and
the current user-facing error strings and 409/422/503 mapping become Jawnix-authored typed
errors with identical text/codes — ScraperKeywords.tsx and its tests must pass unmodified.
Delete the _SAFE_GENERATION_ERRORS allowlist and the upstream generate proxying. Retire
JAWNIX_SCRAPER_OPS_GENERATION_TIMEOUT_SECONDS (its config field, .env.example entry, and
tests); remove nightly.py's ad-hoc max(timeout, 60). Add generator tests: exact and near
duplicates, historical exclusions, malformed responses, truncation, timeouts, rate limits,
adaptive retries, concurrency/lock conflict, and strict exactly-25 failure. Run the complete
suites. Open a PR to main.
```

**Stage B done when:** Jawnix generates keywords itself with the byte-identical
browser contract, drafts and history persist in the Jawnix DB, and the old
timeout setting is gone.

---

## Scraper track — Stage C: rehearsal and cutover (S8–S9)

### Step S8 — Cutover rehearsal against a restored backup

```
Rehearse the Scraper cutover (Stage C, after <S7-ref>) without touching production. Restore
the latest acquisition-database backup into a rehearsal environment per the restore procedure
in docs/OPERATIONS.md and ops/restore.sh. Against it: apply and verify every additive
migration; run the full-history import and verify row counts and checksum against the source
tables; build the vendored Go worker image; stand up scraper-control and run the typed
contract tests against the restored data; run Jawnix pointed at the rehearsal control endpoint
and exercise generation, reads, exports, monitoring, saves, and scheduled controls; then
demonstrate rollback (stop new services, restart the legacy stack, confirm it still answers).
Produce a written rehearsal report — timings for each step, verification outputs, and any
deviations — as the go/no-go artifact for the production window, and draft the maintenance-
window runbook additions for docs/OPERATIONS.md (including retiring the pinned Scale release
reference at cutover). Open a PR with the runbook changes; attach the report.
```

**Done when:** the rehearsal report exists with a demonstrated rollback, and
the runbook PR is merged.

### Step S9 — Production cutover window **[operator]**

```
Execute the Scraper production cutover per the rehearsed runbook in docs/OPERATIONS.md (from
<S8-ref>), with me present throughout — pause for my confirmation before every state-changing
step. Sequence: back up the acquisition database and current deployments; stop legacy Scale
workers and timers; deploy scraper-control and the vendored worker on the acquisition host
against the existing database volume; run the full keyword-history import and verify counts/
checksum; install the OpenRouter secret on the application host; deploy Jawnix (tagged main
revision, via ops/deploy.sh with its dry-run gate) configured for the typed control endpoint;
smoke-test generation, reads, exports, monitoring, saves, enqueueing, and scheduled controls;
start the new workers and timers; disable the legacy Scale dashboard. The stopped Scale stack
and backups are retained for seven days as the rollback path — do not delete anything. Record
every step, timing, and verification output in a cutover log and post it to the tracking
issue.
```

**Done when:** all smoke checks pass on the new stack and the cutover log is
posted. Rollback (through day 7): restore the prior Jawnix image, restart the
old Scale services — migrations stayed additive and backward-compatible.

---

## Retirements — each gated on its own window (P8, S10)

### Step P8 — Retire the legacy static pages (requires explicit approval on #71)

```
Retire the legacy static UI pages, as the separate explicitly-approved change required by the
UI cutover runbook (docs/OPERATIONS.md, "UI cutover (#71)") — confirm with me that approval is
recorded on the issue before starting. Remove index.html, login.html, portal.html,
portal-accept.html, and admin.html from the image and any Compose bind mounts; remove the
now-dead legacy-URL redirect blocks from the Caddyfile only if they reference deleted files
(keep the 302s if external links still depend on them — decide from the cutover-monitor data
and say which you chose); remove $JAWNIX_PUBLIC_BASE_URL/portal-accept.html from the Supabase
Auth redirect allow-list and note that outstanding old-style invitation links die with it.
Update docs/OPERATIONS.md and README.md. Verify the built image serves the shell and that
flag-off rollback is now explicitly documented as no longer available. Open a PR to main.
```

### Step S10 — Remove the Scale remnants (after the 7-day rollback window) **[operator]**

```
The Scraper cutover rollback window has closed — remove the legacy Scale remnants from Jawnix.
Delete the legacy browser proxy/handoff: the /admin/scraper mounts in jawnix/api.py, the
forward_scraper_request/_rewrite_html/_rewrite_css machinery in jawnix/scraper_proxy.py, and
the scraper.jawnix.com blocks in the Caddyfile, docker-compose.yml, and docker-compose.edge.yml.
Update README.md and docs/OPERATIONS.md (drop the Scale dashboard origin-isolation section).
On the hosts (with my confirmation per host): remove the dormant Scale deployment and the old
OpenRouter secret from the acquisition host. Confirm the native /api/admin/scraper/... surface
and the React UI are unaffected by running the full suites and a production smoke of the
scraper workspace. Open a PR to main for the repo changes; log the host cleanups on the
tracking issue.
```

---

## Later — Phase 2 spec (not scheduled here)

### Step X1 — Write the admin-phase overhaul spec

```
Write the spec for the admin phase of the Jawnix UI overhaul, as the successor issue #116
names: admin IA repair, keyword analytics amended to positives-per-delivered (the Worked-Leads
prescriptive machinery is recorded as dormant), and any admin-side items deferred from the
customer-portal phase. Interview me for the open decisions before drafting. File it as a Spec
issue following docs/agents/issue-tracker.md conventions, linked to #46 and #116, and do not
begin implementation.
```

---

## Appendix — grounding facts and overall acceptance

Current state (as of 2026-07-31) that the prompts assume:

- Scraper: all `/api/admin/scraper/...` routes proxy Scale via
  `jawnix/scraper_proxy.py`; HTML parsing lives in `scraper_keywords.py`,
  `scraper_database.py`, `scraper_coverage.py`, `scraper_runtime.py`.
  Monitoring and nightly source segments already speak JSON. Generation uses a
  303-redirect protocol with error strings mapped by `_SAFE_GENERATION_ERRORS`.
  The advisory-lock convention lives in `jawnix_data/scraper.py`. Commit
  `85aa5b0` added the single-request generation timeout that S7 retires.
- UI: UI/UX 01–24 (#47–#70) closed; the #71 flag flip is deployed; #119 tracks
  the smoke walk; #116 is the portal spec; static-page retirement and the
  admin phase are explicitly separate.
- ADRs: 0001 (two-store ownership split), 0015 (portal-primary artifacts),
  0016 (React shell behind a flag, renumbered from a duplicate 0011); the
  migration ADR is 0017 (step S1).

Overall acceptance:

- Scraper: no runtime or deployment dependency on the Scale repository; Jawnix
  directly executes OpenRouter generation; Jawnix reads and controls the
  dedicated Scraper host; all existing Scraper data remains available; a
  successful generation always contains exactly 25 distinct eligible keywords;
  backup restoration and rollback demonstrated before cutover.
- UI: #71 closed with stabilization criteria met; every #116 slice live behind
  the existing gates; static pages retired only by the explicitly approved P8
  change.

Assumptions: "full history" means all historical keyword usage; legacy source
is imported as a provenance-recorded snapshot, not a Git-history merge; the
acquisition database remains on its current host and volume.
