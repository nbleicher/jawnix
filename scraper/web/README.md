# GMS Operations dashboard

FastAPI, HTMX, Tailwind, and asyncpg dashboard for the Postgres control plane.

## Local run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
CONTROL_DIR=../control \
KEYWORDS_PATH=../keywords.txt \
ACTIVE_STATES_PATH=../control/active_states.yaml \
EXPORTS_DIR=../exports/by_state \
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/gmaps_pro \
DASH_PASSWORD=local-dev \
.venv/bin/uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/dashboard` and authenticate as `operator`.

Run `npm install && npm run build:css` after editing templates or `input.css`. The compiled CSS, HTMX, and Lucide bundles are committed under `app/static`, so the production image has no Node runtime or CDN dependency.

## Theme

The UI uses a Terminal CLI design system: dark-only, phosphor green (`#33ff00`) on near-black, JetBrains Mono everywhere (self-hosted under `app/static/fonts/`), zero border radius, no drop shadows. All tokens live in the `:root` block at the top of `input.css`; the legacy `--blue` "info" slot maps to amber (`#ffb000`). Buttons render as `[ LABEL ]` with inverted-video hover, progress bars are segmented `||||` fills, and a `pointer-events:none` scanline overlay (`.crt-overlay` in `base.html`) sits above the page. Animations (blinking status blocks, cursor) respect `prefers-reduced-motion`.

The read pool uses `DATABASE_URL_RO` when set. Production should provide the `gmaps_ro` role and keep the dashboard bound to loopback, as in `docker-compose.box.yml`.
