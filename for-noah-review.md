# For Noah — review sheet

Findings from implementing **#47** (feature-flagged React application shell), 2026-07-27.
Commit `9a3f8f5` on branch `zurich`.

Legend: 🔴 needs your decision · 🟡 worth knowing · 🟢 found and fixed

---

## 🔴 Needs a decision

### 1. Typecheck restored to the image build — RESOLVED AND VERIFIED

**Decision taken (Noah, 2026-07-27): option (b) — the VPS has more RAM.**

**✅ Verified end-to-end in Docker** after raising Colima to 8 GB (see finding 4):

| Check | Result |
|---|---|
| `docker build` with clean source | succeeds; asset hashes match the local build byte-for-byte |
| `docker build` with a deliberate type error | **fails, exit 2**, `error TS2339` — the bug cannot ship |
| Image, flag on | `/app/` 200, deep link 200, hashed asset 200 `immutable` |
| Image, flag off (default) | `/app/*` 404, `/api/healthz` 200 |

`Dockerfile` now runs `npm run build` (`tsc -b && vite build`) again, with
`NODE_OPTIONS=--max-old-space-size=1024` as a guardrail so a runaway typecheck cannot grow
into the co-hosted `buzz-prod` stack. `frontend/package.json`'s `build:bundle` escape hatch
was removed as dead.

**Why the typecheck has to be there.** Vite only *strips* types; it never checks them. I
demonstrated this by introducing a typo (`titel` for `title` on loader data):

| Command | Result |
|---|---|
| `tsc -b` | `error TS2339: Property 'titel' does not exist on type 'PlaceholderData'` |
| `vite build` alone | **exit 0** — bundled and shipped the bug |
| E2E against that bundle | ✗ failed (`h1` rendered empty) — the tests do catch it |

**Measured requirement:** peak RSS **~388 MB** for `tsc -b`, ~237 MB for `vite build`. They run
in sequence, so ~388 MB is the high-water mark. That is small; any normal VPS is fine.

**On the VPS**, sanity-check headroom before relying on a deploy:

```sh
free -m               # want comfortably more than ~400MB available
docker compose build  # now typechecks as part of the build
```

If it ever dies with exit **137**, that is host memory, not the heap cap — the cap produces an
explicit V8 "heap out of memory" message instead. Exit **2** means a genuine type error.

---

### 1b. There is still no CI — the automation gap is open

Restoring `tsc` closes the *typecheck* hole in the deploy path. It does **not** close the
bigger one: **`.github/workflows/` does not exist, there is no husky, no git hooks, and no
lint config.** I checked all four.

So `pytest`, `npm test`, and `npm run test:e2e` — 223 tests that demonstrably catch real
regressions — run only when a human remembers to run them. Nothing gates a
`docker compose build` on the VPS.

Note also that my commit message `9a3f8f5` claims types are "gated by CI". That was wrong when
written; the Dockerfile comment has since been corrected, the commit message text cannot be.

**Still open. Say the word and I'll add a workflow** (`pytest` + `typecheck` + `vitest` +
`playwright`), optionally with pre-commit hooks — there is already a `setup-pre-commit` skill
installed.

---

### 2. react-router upgraded 7 → 8

**What.** `npm audit` flagged **GHSA-qwww-vcr4-c8h2** (high): *React Router RSC Mode CSRF
Bypass*, affecting `7.12.0 – 8.2.0`. The 7.x line has **no fixed version** — latest 7.x is
7.18.1, still in range. Fix is in 8.2.1+.

**My call.** Upgraded to `^8.3.0`. The advisory is RSC-mode-specific and this is a pure
client-side SPA (no RSC, no server-side router — verified by grep), so it did not actually
apply. But upgrading with only a shell built is far cheaper than after 24 more slices, and it
clears the audit.

All 37 unit tests and 58 Playwright tests pass on v8; audit is now clean.

**Flag it if** you have a reason to stay on 7.x.

---

### 3. Python version drift between dev and production

| Where | Version |
|---|---|
| Local `.venv` (what `uv sync` created, what tests ran on) | **3.14.6** |
| `Dockerfile` / `Dockerfile.railway` | **3.12-slim** |
| `pyproject.toml` `requires-python` | `>=3.12` |

**Why it matters.** The 128 passing tests I reported ran on **3.14**, but production runs
**3.12**. `requires-python = ">=3.12"` permits both, so nothing catches a divergence. A
3.14-only behaviour would pass locally and fail in the image.

**Suggested.** Pin CI (once it exists) to 3.12 to match the runtime, or bump the image.

---

## 🟡 Worth knowing

### 4. Local Docker runtime resized — RESOLVED

The container runtime here is **Colima**, not Docker Desktop. It was allocated **2 GB / 2 CPU**
on a 24 GB machine, shared by four projects' containers (Supabase `calllog`, `buzz`, `textro`,
test containers), leaving only ~238 MB available against a 388 MB requirement.

**Done 2026-07-27:**

- Retired the `buzz` containers (`buzz-keycloak`, `buzz-prometheus`, `buzz-minio`).
  `buzz-keycloak` was in an OOM crash-loop (`Restarting (137)`). The named volumes
  `buzz-minio-data`, `buzz-postgres-data`, and `buzz-prometheus-data` were **preserved** —
  `docker rm` without `-v` — so the data is recoverable.
- Raised Colima to **8 GB / 4 CPU** (`colima start --cpu 4 --memory 8`, persisted to
  `~/.colima/default/colima.yaml`).

Available memory inside a container went from **238 MB → 6.3 GB**.

**One casualty, needs your action:** `supabase_edge_runtime_calllog` will not restart. It
bind-mounts an ephemeral Supabase CLI file from a *different* workspace
(`conductor/workspaces/cauli/cayenne/supabase/.temp/start-secrets/...`) that no longer exists.
Fix by running `supabase start` in that workspace. The other 9 `calllog` containers came back
healthy. Two disposable test containers (`calllog-web-test`, `gms-web-test`) also did not
restart — both were `restart=no`.

### 4b. 🔴 Retiring `buzz-prod` breaks `docker-compose.staging.yml`

Removing buzz was not just a doc reference — staging has a hard functional dependency on it.

```yaml
# docker-compose.staging.yml
    ports: !reset []          # publishes NO host ports…
    networks: [private, edge]
networks:
  edge:
    external: true
    name: buzz-prod_buzz-net  # …and joins buzz-prod's network
```

Two consequences now that buzz-prod is gone:

1. **Staging cannot start.** Compose hard-fails when a network declared `external: true` does
   not exist.
2. **Nothing would terminate TLS** for `staging.jawnix.com` even if it did — that was
   buzz-prod's edge Caddy's job.

The silver lining: buzz-prod was the *only* reason staging needed this override. Host ports
80/443 are now free, so staging can work the way production does.

**Options.**
- (a) Delete `docker-compose.staging.yml` and run the base compose with
  `JAWNIX_DOMAIN=staging.jawnix.com`. Simplest, and probably right.
- (b) Keep the override but give it its own network and publish 80/443.
- (c) Leave it if staging is vestigial — production cut over to the VPS on 2026-07-25
  (`OPERATIONS.md:79`), so staging may no longer be used.

**I did not change it.** I only documented the breakage in `OPERATIONS.md`, because I cannot
see the VPS to confirm whether staging is still in use. Tell me which and it is a small change.

### 4c. Local buzz fully removed

Containers, the three named volumes (`buzz-minio-data` 17 kB, `buzz-prometheus-data` 1.4 MB,
`buzz-postgres-data` 0 B — all effectively empty), and the orphaned `buzz-net` network are
gone. Nothing else was attached to any of them.

The `NODE_OPTIONS` heap cap in the `Dockerfile` was also removed: its only justification was
protecting the co-hosted buzz stack, and that stack no longer exists.

### 5. `Dockerfile.railway` does not contain the new shell — and that is correct

Railway builds a completely different image (legacy `app.py` monolith, no `jawnix/` package).
Per `docs/OPERATIONS.md:82,244,253`, **Railway is the retained legacy rollback target**, not
current production. So the shell's absence there is intentional.

Noting it so nobody later "fixes" `Dockerfile.railway` by adding the frontend and
accidentally couples the rollback target to the new UI.

### 6. Pre-existing deprecation that will eventually break the test suite

Every `pytest` run emits:

> `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`

Pre-existing (not from my change), but it affects all 128 tests and will become a hard failure
on a future Starlette release.

### 7. Eight screens are deliberate placeholders

`/app/overview`, `/requests`, `/feedback`, `/account`, `/admin/overview`, `/admin/fulfillment`,
`/admin/acquisition`, `/admin/customers` each render "Not built yet — this screen arrives with
#NN". That is correct for #47 (shell only), but if you click through expecting screens, that is
why.

Real and working: navigation, routing, deep links, landmarks, theming, the design system, and
the full accessibility contract.

### 8. `/app/design-system` is the gate fixture for #70

The gallery route renders every primitive in both themes. The axe sweep and the future
visual-regression baseline (#70) run against it. **New primitives must be added there** or they
get no coverage.

### 9. `AdminShell` hard-codes Acquisition → terminal theme by pathname

`frontend/src/app/shells/AdminShell.tsx:21`. It is the minimal honest demonstration that the
design system supports both themes, but the route→theme binding properly belongs to **#62**.
Commented as such; expect #62 to replace it.

---

## 🟢 Found and fixed (no action needed — context only)

These are listed because they show where the test suite was blind, not because anything is
outstanding.

| # | Finding | How it was caught |
|---|---|---|
| 1 | **The header theme toggle was a dead control.** An effect re-asserted the route theme on every render, reverting the click and writing the losing value to `localStorage`. | Code review, then confirmed in a real browser (`changed: false`). Removed the toggle; extracted `useRouteTheme`; added a regression test. |
| 2 | **Mobile nav active state was colour-only** — a WCAG 1.4.1 failure that axe cannot detect (active `#2563eb` vs muted `#5b6b80` are near-identical in luminance). | Code review. Added an edge bar + weight/underline change. |
| 3 | **E2E specs were never typechecked.** `tsconfig.app.json` covered only `src`. | Code review. Adding them surfaced 1 unused import and 6 missing-DOM-lib errors. |
| 4 | **Playwright never exercised the FastAPI serving path.** `vite preview`'s own SPA fallback satisfied the "direct navigation" and asset tests — they would have passed with `jawnix/frontend.py` deleted. | Code review. Added `tests/test_frontend_shell_integration.py` driving the real bundle through the real app. |
| 5 | **My contrast annotations in `tokens.css` were wrong**, two of them swapped. | Code review. Computed all 15 pairs — all genuinely pass AA; only the comments lied. Corrected. |
| 6 | **Two of my own test assertions were wrong.** A traversal case the HTTP client normalised before it reached the server; a dialog focus assertion too strict for native `<dialog>` cycling. | Verified empirically before changing — the dialog background *is* genuinely inert (0 interactive elements reachable across 12 tabs). |

---

## Verification actually performed

- **37** unit tests (vitest), **58** Playwright journeys against the compiled bundle
  (mobile + desktop), **128** Python tests (2 skipped — pre-existing environment gates).
- axe WCAG 2.2 AA sweep over every route, plus the gallery in both themes.
- **Docker image built and run end-to-end**: flag on → shell, deep links, and immutably cached
  hashed assets served; flag off → 404 everywhere under `/app` with the API healthy.

Not performed: manual screen-reader testing, real-device testing, visual regression
(all belong to #70).
