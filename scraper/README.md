# scraper/ — the Jawnix-owned Scraper host

Jawnix owns the Scraper system in this repository (ADR 0017). This directory is
the deployment source for the acquisition host: the typed control plane, the
vendored Go worker, migrations, and scheduled operations.

## Live docs

- **Control plane:** `web/README.md` — the headless FastAPI `scraper-control`
  service Jawnix calls over WireGuard; `SCRAPER_BASELINE.md` — source baseline,
  topology, and the reproducible baseline check.
- **Worker:** `worker/` — vendored `gmapssaas` source (`worker/src/`), image
  build, shipper, and box provisioning (`../bootstrap-box.sh`).
- **Enqueue and queue operations:** `QUEUE_OPERATIONS.md` — backpressure,
  healthy signals, retention, and worker scaling.
- **Deployment and cutover:** `../docs/OPERATIONS.md` — two-host WireGuard
  topology, provisioning, deploys, and the cutover/rollback runbooks.
- **Provenance:** `PROVENANCE.md` — where this source came from.

Completed and superseded planning documents live in
`../docs/plans/archive/`.
