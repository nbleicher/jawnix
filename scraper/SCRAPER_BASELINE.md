# Authoritative Scraper source baseline

The `nbleicher/scale` repository is the authoritative source for the Google
Maps Scraper operations dashboard, its Python control services, and the
deployment topology. The worker implementation is fetched from
`nbleicher/scraper` at the immutable revision
`7caca2ce8122c0ffaf47ca5737a06d05a23a90ca`, plus the tracked,
build-only resource controls in `scraper_changes/worker-build.patch`. The
dashboard is the FastAPI/HTMX application under `web/`; the React project in
Conductor Playground supplied its Terminal CLI design language but is not a
second production control plane.

## Revisions

- Pre-stabilization source revision: `3da7dfc`
- Rollback tag: `scraper-source-rollback-2026-07-27` (pinned to `3da7dfc`)
- Stabilized baseline tag: `scraper-source-baseline-2026-07-27-v4`
- Running production revision: unverified; the live service exposes no source
  revision and no production access was used during stabilization.

The stabilization records source and tests only. It does not restart, rebuild,
or otherwise change the running Scraper service. Do not deploy the baseline
until the operator records the currently running image or source revision as
the production rollback point.

## Reproduce the dashboard and control-plane check

Start an isolated PostgreSQL 16 database, then run:

```bash
TEST_DATABASE_URL=postgresql://postgres:baseline-test@127.0.0.1:55439/gms_baseline_test \
  scripts/check-baseline.sh
```

The check performs all of the following from declared source:

1. verifies that the dashboard, control services, migrations, systemd units,
   and Docker deployment inputs are tracked;
2. fetches the worker repository at its exact recorded revision;
3. creates a fresh Python environment and installs the pinned dependencies;
4. rebuilds the committed Tailwind asset and rejects generated drift;
5. applies the real additive Scraper migrations to the isolated database;
6. runs the dashboard HTTP and control-service tests;
7. validates the Compose topology; and
8. builds both production dashboard and worker images.

The check refuses any database URL whose database name is not
`gms_baseline_test`. That database must be isolated because the suite resets
and seeds its tables.

## Service topology

- `dashboard`: FastAPI/HTMX operator surface, bound to loopback port 8090.
- `db`: PostgreSQL control data, bound to loopback.
- `worker`: gmapssaas worker service using the pinned owned scraper source.
- `migrate`: tool profile that applies upstream and scale migrations.
- `control/`: enqueue, export, alert, rollover, and uptime services.
- `worker/heartbeat.py`: stack and worker telemetry collector.
- `systemd/`: recurring control-service and telemetry units.

Secrets stay in environment files derived from `.env.box.example`; runtime
keywords, exports, virtual environments, caches, and local `.env` files are
excluded from deployable source.
