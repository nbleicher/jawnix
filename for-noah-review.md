# For Noah — review sheet

Findings from implementing **#47** (feature-flagged React application shell) and the follow-up
infrastructure work.

Last updated 2026-07-28. Build order: [ui-rebuild-order.md](ui-rebuild-order.md).

| | State |
|---|---|
| Branch | `implement-issues-47-71-spec-46`, pushed |
| Pull request | [#72](https://github.com/nbleicher/jawnix/pull/72) — **OPEN, MERGEABLE, CLEAN** |
| CI | **green** on `93a97d3` — frontend, backend, and image jobs all pass |
| Issues #46–#71 | all still OPEN; #47 closes when #72 merges |
| Waiting on you | merge #72, then decide how to resource #49 (item A) |

Legend: 🔴 open, needs your decision · 🟡 worth knowing · 🟢 resolved

---

## 🔴 Open — needs a decision

### A. #49 (administrator MFA) is a large security slice, not a screen

Next on the critical path, and materially bigger than #47. Today `require_admin`
(`jawnix/auth.py:90`) checks **only** `role != "admin"` — there is no MFA, no assurance level,
and no factor state anywhere in the codebase. It guards **39 endpoints**.

Delivering #49 honestly means:

- Supabase TOTP enrolment with a **primary and a separately stored backup factor**;
- capturing the Supabase access token's assurance level (`aal`) into the signed session, and
  enforcing **aal2 in the backend** — the criteria explicitly reject a frontend-only gate;
- validating live factor state against Supabase rather than trusting the token;
- rejecting stale, revoked, replayed, and Customer-scoped sessions on admin routes;
- a **documented break-glass** path with explicit authorisation and a complete audit entry;
- accessible enrolment / challenge / retry / cancellation / lost-device screens;
- browser and API tests for assurance, dual enrolment, recovery, CSRF, and secret redaction.

**Recommendation: give #49 its own session or workspace.** A half-built authentication gate is
worse than none, and this is the slice that protects every administrator surface. It should not
be squeezed in beside unrelated work.

---

### B. The `calllog` Supabase stack cannot be moved into a Jawnix workspace

**Authorised by Noah 2026-07-28 to move it here and run `supabase start`. Not carried out —
the premise does not hold.** Recording rather than executing.

Three facts, each verified:

| Check | Result |
|---|---|
| Owning repository | `conductor/workspaces/cauli/cayenne` is a checkout of **`nbleicher/cauli.git`** — a different repo from `nbleicher/jawnix` |
| Its Supabase project | `supabase/config.toml` → `project_id = "calllog"` |
| Jawnix's Supabase project | **none.** No `supabase/` directory, no `config.toml` |
| How Jawnix reaches Supabase | **hosted**: `JAWNIX_SUPABASE_URL=https://YOUR_PROJECT.supabase.co` |

So the stack is the **cauli/calllog** product's local development database, not Jawnix's. Moving
it under a Jawnix workspace would misfile another product's database, and `supabase start` in
this repository would fail outright because there is no Supabase project here to start.

Jawnix does not use a local Supabase stack at all — it talks to a hosted project, which is why
its 128 tests pass with the `calllog` containers stopped.

**To fix the actual problem**, run `supabase start` in the cauli workspace that owns it:

```sh
cd /Users/noahbleicher/conductor/workspaces/cauli/cayenne && supabase start
```

**If you did mean to give Jawnix its own local Supabase**, that is a different and larger piece
of work — `supabase init`, a schema baseline from `supabase-schema.sql`, and pointing
`JAWNIX_SUPABASE_URL` at the local instance. Worth doing deliberately, not as a side effect of
restarting a container. Note the stray `supabase/.temp/` line in `.gitignore` is the only trace
of a local Supabase in this repo and looks like carried-over boilerplate.

*Aside, possibly useful for #49:* `cauli`'s config already enables `[auth.mfa.totp]`. If that
project has working TOTP enrolment, it is a reference implementation worth reading before
building Jawnix's.

---

## 🟡 Worth knowing

### C. `supabase_edge_runtime_calllog` is still down — not a Jawnix issue

Casualty of the Colima restart. It bind-mounts an ephemeral Supabase CLI file from a *different*
workspace that no longer exists:

```
conductor/workspaces/cauli/cayenne/supabase/.temp/start-secrets/.../index.ts
```

**Fix:** run `supabase start` in the `cauli/cayenne` workspace. I did not patch another
project's workspace.

The other 9 `calllog` containers came back healthy. Two disposable test containers
(`calllog-web-test`, `gms-web-test`) also did not restart — both were `restart=no`.

### D. `Dockerfile.railway` has no frontend — and that is correct

Railway builds a different image entirely (legacy `app.py` monolith, no `jawnix/` package). Per
`OPERATIONS.md:82,244,253`, **Railway is the retained legacy rollback target**, not current
production.

Noted so nobody later "fixes" it by adding the frontend and couples the rollback target to the
new UI.

### E. Pre-existing deprecation that will eventually break the test suite

Every `pytest` run emits:

> `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`

Not from my change, but it affects all 128 tests and becomes a hard failure on a future
Starlette release.

### F. Eight screens are deliberate placeholders

`/app/overview`, `/requests`, `/feedback`, `/account`, `/admin/overview`, `/admin/fulfillment`,
`/admin/acquisition`, `/admin/customers` each render "Not built yet — this screen arrives with
#NN". Correct for #47 (shell only), but that is why clicking through shows no screens.

Real and working: navigation, routing, deep links, landmarks, theming, the design system, and
the full accessibility contract.

### G. `/app/design-system` is the gate fixture for #70

The gallery renders every primitive in both themes. The axe sweep and the future
visual-regression baseline (#70) run against it. **New primitives must be added there** or they
get no coverage.

### H. `AdminShell` hard-codes Acquisition → terminal theme by pathname

`frontend/src/app/shells/AdminShell.tsx:21`. The minimal honest demonstration that the design
system supports both themes, but the route→theme binding properly belongs to **#62**. Commented
as such; expect #62 to replace it.

---

## 🟢 Resolved

### Staging override deleted; the monitor it would have broken was fixed with it

`docker-compose.staging.yml` is gone. Staging now runs the base stack:

```sh
JAWNIX_DOMAIN=staging.jawnix.com docker compose up -d
```

**Deleting it alone would have broken production.** `ops/cutover-monitor.sh:13` passed
`-f docker-compose.staging.yml` with `2>/dev/null`, so a missing file makes Compose fail
silently, `running_services` resolve to **0**, and the five-minute monitor flip to *unhealthy*
and fire a Telegram alert. I reproduced that exact failure before changing the script to use the
base compose. `OPERATIONS.md` updated in the same commit.

### CI caught a latent test bug that predates this branch

`tests/test_cli.py` (from `a911d53`, untouched by this branch) asserted
`"--request-id" in redistribute.stdout`. Rich colourises help output whenever it believes it is
writing to a terminal — **and it treats GitHub Actions as one**. It styles each option name, so
the escape codes land *inside* the token and the raw substring is absent even though the option
is documented correctly.

Narrowed it by elimination rather than guessing:

| Condition | `--request-id` found |
|---|---|
| local default | ✅ |
| `COLUMNS=80` (first theory — wrong) | ✅ |
| `TERM=dumb` | ✅ |
| `NO_COLOR=1` | ❌ does not override CI detection |
| **`GITHUB_ACTIONS=true`** | ❌ **reproduced** |

The test now asserts on the visible text with styling stripped, which is what it always meant,
and the compound assertion is split so a failure names the offending command. No other test
asserts on CLI stdout.

**This is the case for CI in one example**: that test would have passed locally forever and
failed the first time anyone ran it in an automated environment.

### CI added — and it caught a real defect on its first run

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | Covers |
|---|---|
| **frontend** | typecheck · vitest · build · Playwright (Chromium) · uploads the bundle |
| **backend** | pytest against that bundle, so the shell integration tests run instead of skipping |
| **image** | builds the deployment image, smoke-tests **both** flag states, deep links, immutable caching |

The first run **failed** — `Unable to resolve action astral-sh/setup-uv@v9`. That repository
publishes `v9.0.0` but stopped publishing floating major tags after v7.6. Pinned to the exact
version. Exactly the class of thing that would otherwise have been discovered during a deploy.

### Python drift closed

The backend job pins **3.12** and fails the build if it resolves anything else; `.python-version`
pins local development the same way. The full suite passes on 3.12 — **128 passed, 2 skipped** —
so the drift was latent rather than actual, but it can no longer return silently.

### #47 raised for review

[PR #72](https://github.com/nbleicher/jawnix/pull/72) against `main`, closing #47 on merge.

### Typecheck restored to the image build — verified end to end

**Your call: option (b), the VPS has more RAM.** `Dockerfile` runs `npm run build`
(`tsc -b && vite build`) again; the `build:bundle` escape hatch was removed as dead.

**Why it matters.** Vite only *strips* types, never checks them. Demonstrated with a typo
(`titel` for `title` on loader data):

| Command | Result |
|---|---|
| `tsc -b` | `error TS2339: Property 'titel' does not exist` |
| `vite build` alone | **exit 0** — bundled and shipped the bug |
| E2E against that bundle | ✗ failed (`h1` rendered empty) |

**Verified in Docker** after the Colima resize:

| Check | Result |
|---|---|
| `docker build`, clean source | succeeds; asset hashes match the local build byte-for-byte |
| `docker build`, deliberate type error | **fails, exit 2**, `TS2339` — the bug cannot ship |
| Image, flag on | `/app/` 200 · deep link 200 · hashed asset 200 `immutable` |
| Image, flag off (default) | `/app/*` 404 · `/api/healthz` 200 |

Measured peak RSS: **~388 MB** (`tsc`), ~237 MB (`vite`), run in sequence. On the VPS, exit
**137** means host memory; exit **2** means a real type error.

### Container runtime resized, buzz fully retired

Runtime is **Colima**, not Docker Desktop. It had **2 GB / 2 CPU** on a 24 GB machine, shared by
four projects, leaving **238 MB** available against a 388 MB need.

- Retired buzz: containers, three named volumes (all effectively empty — 0 B / 17 kB / 1.4 MB),
  and the orphaned `buzz-net` network. `buzz-keycloak` had been in an OOM crash-loop
  (`Restarting (137)`).
- Raised Colima to **8 GB / 4 CPU**, persisted to `~/.colima/default/colima.yaml`.

Available memory inside a container: **238 MB → 6.3 GB**.

The `NODE_OPTIONS` heap cap was also removed from the `Dockerfile` — its only justification was
protecting the co-hosted buzz stack, which no longer exists.

### react-router upgraded 7 → 8

`npm audit` flagged **GHSA-qwww-vcr4-c8h2** (high, *RSC Mode CSRF Bypass*), affecting
`7.12.0 – 8.2.0`. The 7.x line has **no fixed version**; the fix is in 8.2.1+.

Upgraded to `^8.3.0`. The advisory is RSC-specific and does not apply to this SPA (no RSC, no
server-side router — verified), but upgrading with only a shell built is far cheaper than after
24 more slices. All tests pass on v8; audit is clean.

### Bugs the code review caught that my tests missed

Listed because they show where the suite was blind.

| Finding | How it was caught |
|---|---|
| **The header theme toggle was a dead control.** An effect re-asserted the route theme every render, reverting the click and writing the losing value to `localStorage`. | Review, then confirmed in a real browser (`changed: false`). Removed the toggle, extracted `useRouteTheme`, added a regression test. |
| **Mobile nav active state was colour-only** — WCAG 1.4.1, undetectable by axe (`#2563eb` vs `#5b6b80` are near-identical in luminance). | Review. Added an edge bar plus weight/underline change. |
| **E2E specs were never typechecked.** `tsconfig.app.json` covered only `src`. | Review. Adding them surfaced 1 unused import and 6 missing-DOM-lib errors. |
| **Playwright never exercised the FastAPI serving path.** `vite preview`'s own SPA fallback satisfied the tests — they would have passed with `jawnix/frontend.py` deleted. | Review. Added `tests/test_frontend_shell_integration.py` driving the real bundle through the real app. |
| **Contrast annotations in `tokens.css` were wrong**, two swapped. | Review. Computed all 15 pairs — all genuinely pass AA; only the comments lied. |
| **Two of my own test assertions were wrong.** A traversal case the HTTP client normalised before it reached the server; a dialog focus assertion too strict for native `<dialog>` cycling. | Verified empirically before changing — the dialog background *is* inert (0 interactive elements reachable across 12 tabs). |

---

## Verification performed

**223 automated tests, now running in CI on every pull request** — verified green on the exact
commit rather than inferred:

| Suite | Count |
|---|---|
| vitest (unit) | 37 |
| Playwright, against the compiled bundle, mobile + desktop | 58 |
| pytest | 128 (2 skipped — pre-existing environment gates) |

Also covered: axe WCAG 2.2 AA sweep over every route plus the gallery in both themes; Docker
image built and exercised end to end in both feature-flag states; a deliberate type error
confirmed to fail the image build.

**Not performed** (all belong to #70): manual screen-reader testing, real-device testing,
visual regression.

---

## A note on my own reliability

Three times in this work my *measurement* was wrong while the code was fine. Recording it so the
sheet is not read as more certain than it is:

1. **`docker build` failed** and I initially attributed it to `tsc` being memory-hungry. The real
   cause was a 1.9 GB Colima VM shared with three other projects. Diagnosed properly only after
   measuring available memory inside a container.
2. **A CI monitor reported a stale failure as current** — it polled "latest run" and caught the
   previous commit's result before the new run registered. I reported it before spotting the
   race, then corrected it and re-armed pinned to the SHA.
3. **Two of my own test assertions were wrong**, not the code they tested.

In each case the fix was to verify empirically rather than reason from plausibility. Where this
sheet says "verified", it means a command was run and its output read.
