# For Noah — review sheet

Findings from implementing **#47** (feature-flagged React application shell), 2026-07-27.
Commit `9a3f8f5` on branch `zurich`.

Legend: 🔴 needs your decision · 🟡 worth knowing · 🟢 found and fixed

---

## 🔴 Needs a decision

### 1. There is no CI, and I removed typechecking from the image build

**What.** The Docker image build runs `vite build` without `tsc -b`, because `tsc -b` is
OOM-killed in the build container (exit 137, verified — see #4 below).

**Why it matters.** I originally justified this as "types are gated in CI". Then I checked:
**`.github/workflows/` does not exist. This repo has no CI at all.** So nothing enforces
`npm run typecheck`, `npm test`, or `pytest` before an image is built or deployed. A type
error can reach a production image.

My commit message `9a3f8f5` repeats the incorrect "and CI" claim. I corrected the Dockerfile
comment afterwards; the commit message text is still wrong.

**Options.**
- (a) Add a GitHub Actions workflow running `pytest`, `npm run typecheck`, `npm test`,
  `npm run test:e2e`. Recommended — the rebuild has 24 more slices and no safety net.
- (b) Give the build host more memory and restore `tsc -b` to the image build.
- (c) Accept the risk and rely on local discipline.

**I have not done any of these.** Tell me which and I'll implement it.

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

### 4. The build container has 1.9GB / 2 CPUs, and `tsc` does not fit

Measured: `docker info` → `Total Memory: 1.913GiB`, `CPUs: 2`. Isolated the failure —
`vite build` alone succeeds; `tsc -b` is killed even with `--max-old-space-size=1400`.

If your real build host is larger this is a non-issue and option 1(b) above is the clean fix.
Worth confirming what the actual deployment builder has.

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
