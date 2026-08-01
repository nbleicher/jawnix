# Scraper source provenance

This subsystem was imported on 2026-07-31 as source snapshots. No Git history
from either source repository was merged into Jawnix.

## Scale control plane

- Source: <https://github.com/nbleicher/scale>
- Release tag: `scraper-source-baseline-2026-07-27-v4`
- Resolved commit: `a57fcfadd387d068e706eec4925d368dd2fadd98`
- Imported path: `scraper/`

The tracked tree at that release supplied the control logic, database
migrations, deployment configuration, monitoring and export utilities,
pipeline controls, keyword rollover tasks, and scheduled operations. The
"Conductor Playground" React project mentioned by Scale's
`SCRAPER_BASELINE.md` supplied visual design language only and was not used as
a source for this import. Statements in that snapshot that call Scale the
authoritative source describe the legacy state at the time of the tag; ADR
0017 records Jawnix's ownership from this import forward.

### JSON contract compatibility absorbed after the snapshot

The headless control service also absorbs the production JSON behavior Jawnix
was already calling after the baseline tag. These files were used as migration
inputs, then consolidated under `web/app/contracts.py` and
`web/app/control_api.py`:

- explicit Source Segments: Scale commits
  `1b4d72b746f4504dc6d33ca1a28b2440e3b84334`,
  `fbd168206d3277e7eebd79f5d17e488758f4a38a`, and
  `da48c0f39f86b772db430dbc3fb9eed720066fb7`;
- dashboard JSON projection: Scale commit
  `3ea1751e684f4332d82f46fd07d8b22c24ad7cec`.

Those compatibility endpoints are now owned here; the temporary patch that
applied the dashboard projection back to Scale has been deleted.

## Go worker

- Source: <https://github.com/nbleicher/scraper>
- Immutable revision: `7caca2ce8122c0ffaf47ca5737a06d05a23a90ca`
- Imported path: `scraper/worker/src/`
- License: `scraper/worker/src/LICENSE` (MIT)
- Applied patch: `scraper/scraper_changes/worker-build.patch`
- Patch SHA-256: `a4e11a53b32a6dba4a518dd01f91513fe613f9baf24eff78ce22ef74b2f962ff`
- Patched `Dockerfile.saas` Git blob: `d7dc0cf3e8bb45abb46643edbb0efb6e53b9594f`

The Scale patch only adds configurable Go build flags, garbage-collector
pressure, and a single build process to limit build-time resource use. The
tracked generated `google-maps-scraper` binary and `results.csv` were excluded
from the vendored snapshot, as were tracked `.DS_Store` metadata files.
