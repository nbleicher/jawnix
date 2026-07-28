# Jawnix UI rebuild — order of operations

Build order for **#46 "Rebuild the active Jawnix Platform UI and UX"** and its 25 slices
(#47–#71). Derived from the `## Blocked by` section of each issue.

Last updated 2026-07-28. Companion sheet: [for-noah-review.md](for-noah-review.md).

---

## Status

| | Count |
|---|---|
| ✅ Built, in review | 1 (#47) |
| 🔓 Unblocked, ready to start | 2 (#48, #49) |
| 🔒 Blocked | 22 |

**#47 is in review** — [PR #72](https://github.com/nbleicher/jawnix/pull/72), CI green,
`MERGEABLE`/`CLEAN`, closing #47 on merge. All 26 issues (#46–#71) remain OPEN until then.

**Start #49 before #48.** Both are unblocked, but #49 is on the critical path and gates 17 of
the 24 remaining issues; #48 gates 7. See the caution on #49's size in
[for-noah-review.md](for-noah-review.md) item A.

Later slices should branch from `main` once #72 merges — or from
`implement-issues-47-71-spec-46` if you want to start before then — so #47 stays reviewable as a
discrete unit.

---

## Dependency table

| # | Title | Blocked by | Blocks (transitively) |
|---|---|---|---|
| **47** ✅ | Feature-flagged React application shell | — | **24** |
| **48** 🔓 | Customer sign-in and invitation acceptance | #47 | 7 |
| **49** 🔓 | Administrator MFA and recovery ⭐ | #47 | **17** |
| 50 | Customer Overview | #48 | 6 |
| 51 | Guided Batch Requests + milestone graph | #50 | 3 |
| 52 | Batch Request milestone emails | #51 | 2 |
| 53 | Guided Customer Feedback | #50 | 2 |
| 54 | Safe Licensed State management | #50 | 2 |
| 55 | Administrator navigation + Operations overview ⭐ | #49 | **16** |
| 56 | Global and entity-level Activity ⭐ | #55 | **15** |
| 57 | Core Fulfillment operations | #56 | 3 |
| 58 | Lead Report and eligibility controls | #56 | 2 |
| 59 | Customer and User Account management | #56 | 4 |
| 60 | Agency management + permanent-history merging | #59 | 3 |
| 61 | One-time User Account migration | #59, #60 | 2 |
| 62 | Native Scraper security + terminal workspace ⭐ | #56 | **9** |
| 63 | Scraper monitoring and pipeline controls | #62 | 2 |
| 64 | Scraper state and grid coverage | #62 | 2 |
| 65 | Scraper keyword management | #62 | 2 |
| 66 | Scraper campaign history + runtime configuration | #62 | 2 |
| 67 | Scraper database browsing and exports | #62 | 2 |
| 68 | Unified acquisition review and optimization ⭐ | #62 | 3 |
| 69 | Telegram / Jawnix action convergence ⭐ | #57, #68 | 2 |
| 70 | Parity, migration, and accessibility gates ⭐ | #52, #53, #54, #57, #58, #60, #61, #63, #64, #65, #66, #67, #68, #69 | 1 |
| 71 | Controlled UI cutover ⭐ | #70 | — |

⭐ = on the critical path.

---

## Execution waves

Everything in a wave can run in parallel once the previous wave lands.

| Wave | Issues | Parallelism |
|---|---|---|
| **0** ✅ | #47 | done |
| **1** | **#49** ⭐ · #48 | 2 |
| **2** | **#55** ⭐ (←49) · #50 (←48) | 2 |
| **3** | **#56** ⭐ (←55) · #51, #53, #54 (←50) | 4 |
| **4** | **#62** ⭐ (←56) · #57, #58, #59 (←56) · #52 (←51) | 5 |
| **5** | **#68** ⭐, #63, #64, #65, #66, #67 (←62) · #60 (←59) | **7** |
| **6** | **#69** ⭐ (←57, #68) · #61 (←59, #60) | 2 |
| **7** | **#70** ⭐ | 1 |
| **8** | **#71** ⭐ | 1 |

**Critical path (8 steps remaining):**

```
#47 ✅ → #49 → #55 → #56 → #62 → #68 → #69 → #70 → #71
```

---

## What this implies

**Start #49 before #48.** Both are unblocked, but #49 is on the critical path and #48 is not.
#49 → #55 → #56 gates **17 of the 24 remaining issues**. With one agent, run #49. With two,
run both.

**#56 is the worst bottleneck in the plan.** A single issue about Activity views blocks
Fulfillment (#57), eligibility (#58), Customers (#59), *and* the entire Scraper branch (#62 →
#63–#68). Nothing in waves 4–8 can start until it lands.

> **Worth challenging.** If Fulfillment, Customers, and Scraper only need Activity *later*,
> splitting #56 into "activity plumbing" and "activity screens" would unlock four parallel
> tracks much earlier. This is the single highest-leverage change available to the schedule.

**Peak parallelism is wave 5** — 7 issues at once, 6 of them Scraper. That is where extra
agents pay off most. Waves 1–2 are strictly serial pairs no matter how many agents you have.

**Waves 7–8 are not agent-completable.**

- **#70** requires manual screen-reader acceptance and approved visual-regression sign-off.
- **#71** is a production cutover: real database backup, DNS, monitoring, and a verified
  rollback path.

Plan both as human-led, with agents preparing evidence rather than executing.

---

## Definition of done for each slice

Established by #47 and **enforced by `.github/workflows/ci.yml` on every pull request** — three
jobs (frontend, backend, image), currently green. It has already caught two real defects, so
treat a red build as a finding rather than noise:

- `npm run typecheck`, `npm test`, `npm run test:e2e` and `uv run pytest` all green.
- New primitives added to `/app/design-system`, or they get no accessibility or
  visual-regression coverage.
- New routes covered by the axe WCAG 2.2 AA sweep in `e2e/accessibility.spec.ts`.
- Screens read their data through a route loader, so the pending and error seams the shell
  provides are actually used.
- Anything touching the serving path covered in `tests/test_frontend_shell_integration.py`,
  which runs against the real compiled bundle rather than a fixture.

## Ordering caveats

- **#70's `Blocked by` list omits some ancestors** (#48, #50, #51, #55, #59, #62). They are
  covered transitively, so the graph is still correct — but do not read that list as the
  complete set of prerequisites.
- **#61 (migration) is late but high-risk.** It depends on #59 and #60 and lands in wave 6,
  leaving little room before the #70 gate. Its dry-run rehearsal is worth starting as soon as
  #59 is stable rather than waiting for the wave.
- **#62 gates six Scraper slices at once.** It is the second-largest bottleneck after #56 and
  carries the step-up MFA and private-service security work, so it deserves the most careful
  review of any single slice.
