# For Noah — review sheet

Findings from implementing **#47** (feature-flagged React application shell) and the follow-up
infrastructure work.

Last updated 2026-07-28. Branch `implement-issues-47-71-spec-46`, 5 commits, **not pushed, no
PR**. Build order: [ui-rebuild-order.md](ui-rebuild-order.md).

Legend: 🔴 open, needs your decision · 🟡 worth knowing · 🟢 resolved

---

## 🔴 Open — needs a decision

### A. Retiring `buzz-prod` breaks `docker-compose.staging.yml`

Removing buzz was not just a documentation reference. Staging has a **hard functional
dependency** on it:

```yaml
# docker-compose.staging.yml
    ports: !reset []            # publishes NO host ports…
    networks: [private, edge]
networks:
  edge:
    external: true
    name: buzz-prod_buzz-net    # …and joins buzz-prod's network
```

Two consequences:

1. **Staging cannot start.** Verified the exact failure —
   `network buzz-prod_buzz-net declared as external, but could not be found`.
2. **Nothing terminates TLS** for `staging.jawnix.com` even if it could. That was buzz-prod's
   edge Caddy's job.

The silver lining: buzz-prod was the *only* reason staging needed this override. Host ports
80/443 are now free, so staging can work the way production does.

**Options.**
- **(a)** Delete `docker-compose.staging.yml`; run the base compose with
  `JAWNIX_DOMAIN=staging.jawnix.com`. Simplest, probably right.
- **(b)** Keep the override, give it its own network and publish 80/443.
- **(c)** Leave it — production cut over to the VPS on 2026-07-25 (`OPERATIONS.md:79`), so
  staging may be vestigial.

**Not changed.** I documented the breakage in `OPERATIONS.md` only, because choosing needs a
view of the VPS I do not have.

---

### B. There is no CI — nothing runs the tests automatically

**`.github/workflows/` does not exist. No husky, no git hooks, no lint config.** I checked all
four.

`pytest`, `npm run typecheck`, `npm test`, and `npm run test:e2e` — **223 tests that
demonstrably catch real regressions** — run only when a human remembers. Nothing gates a
`docker compose build` on the VPS.

This is now the largest remaining risk to the rebuild: 24 more slices will be built on top of
this shell with no automated safety net.

**Offer.** A workflow running `pytest` + `typecheck` + `vitest` + `playwright`, optionally with
pre-commit hooks (a `setup-pre-commit` skill is already installed). Pin it to **Python 3.12** to
close item D at the same time.

---

### C. #47 is complete but still open, unpushed, and unreviewed

5 commits sit on `implement-issues-47-71-spec-46` with no remote branch and no PR. Issue #47 is
still `OPEN`. I have not pushed or closed anything — both are outward-facing actions.

Tell me when you want a PR raised against `main`.

---

### D. Python version drift between dev and production

| Where | Version |
|---|---|
| Local `.venv` (what the 128 tests ran on) | **3.14.6** |
| `Dockerfile` / `Dockerfile.railway` | **3.12-slim** |
| `pyproject.toml` `requires-python` | `>=3.12` |

`requires-python = ">=3.12"` permits both, so nothing catches a divergence. A 3.14-only
behaviour passes locally and fails in the image. Closed for free by pinning CI to 3.12 (item B).

---

## 🟡 Worth knowing

### E. `supabase_edge_runtime_calllog` is still down — not a Jawnix issue

Casualty of the Colima restart. It bind-mounts an ephemeral Supabase CLI file from a *different*
workspace that no longer exists:

```
conductor/workspaces/cauli/cayenne/supabase/.temp/start-secrets/.../index.ts
```

**Fix:** run `supabase start` in the `cauli/cayenne` workspace. I did not patch another
project's workspace.

The other 9 `calllog` containers came back healthy. Two disposable test containers
(`calllog-web-test`, `gms-web-test`) also did not restart — both were `restart=no`.

### F. `Dockerfile.railway` has no frontend — and that is correct

Railway builds a different image entirely (legacy `app.py` monolith, no `jawnix/` package). Per
`OPERATIONS.md:82,244,253`, **Railway is the retained legacy rollback target**, not current
production.

Noted so nobody later "fixes" it by adding the frontend and couples the rollback target to the
new UI.

### G. Pre-existing deprecation that will eventually break the test suite

Every `pytest` run emits:

> `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`

Not from my change, but it affects all 128 tests and becomes a hard failure on a future
Starlette release.

### H. Eight screens are deliberate placeholders

`/app/overview`, `/requests`, `/feedback`, `/account`, `/admin/overview`, `/admin/fulfillment`,
`/admin/acquisition`, `/admin/customers` each render "Not built yet — this screen arrives with
#NN". Correct for #47 (shell only), but that is why clicking through shows no screens.

Real and working: navigation, routing, deep links, landmarks, theming, the design system, and
the full accessibility contract.

### I. `/app/design-system` is the gate fixture for #70

The gallery renders every primitive in both themes. The axe sweep and the future
visual-regression baseline (#70) run against it. **New primitives must be added there** or they
get no coverage.

### J. `AdminShell` hard-codes Acquisition → terminal theme by pathname

`frontend/src/app/shells/AdminShell.tsx:21`. The minimal honest demonstration that the design
system supports both themes, but the route→theme binding properly belongs to **#62**. Commented
as such; expect #62 to replace it.

---

## 🟢 Resolved

### K. Typecheck restored to the image build — verified end to end

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

### L. Container runtime resized, buzz fully retired

Runtime is **Colima**, not Docker Desktop. It had **2 GB / 2 CPU** on a 24 GB machine, shared by
four projects, leaving **238 MB** available against a 388 MB need.

- Retired buzz: containers, three named volumes (all effectively empty — 0 B / 17 kB / 1.4 MB),
  and the orphaned `buzz-net` network. `buzz-keycloak` had been in an OOM crash-loop
  (`Restarting (137)`).
- Raised Colima to **8 GB / 4 CPU**, persisted to `~/.colima/default/colima.yaml`.

Available memory inside a container: **238 MB → 6.3 GB**.

The `NODE_OPTIONS` heap cap was also removed from the `Dockerfile` — its only justification was
protecting the co-hosted buzz stack, which no longer exists.

### M. react-router upgraded 7 → 8

`npm audit` flagged **GHSA-qwww-vcr4-c8h2** (high, *RSC Mode CSRF Bypass*), affecting
`7.12.0 – 8.2.0`. The 7.x line has **no fixed version**; the fix is in 8.2.1+.

Upgraded to `^8.3.0`. The advisory is RSC-specific and does not apply to this SPA (no RSC, no
server-side router — verified), but upgrading with only a shell built is far cheaper than after
24 more slices. All tests pass on v8; audit is clean.

### N. Bugs the code review caught that my tests missed

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

- **37** unit tests (vitest) · **58** Playwright journeys against the compiled bundle
  (mobile + desktop) · **128** Python tests (2 skipped — pre-existing environment gates).
- axe WCAG 2.2 AA sweep over every route, plus the gallery in both themes.
- Docker image built and run end to end, both flag states.

**Not performed** (all belong to #70): manual screen-reader testing, real-device testing,
visual regression.
