# Scraper Control

Headless FastAPI and asyncpg control process for the acquisition host. It has
no operator UI: Jawnix is the only operator surface and calls these typed JSON
operations over WireGuard.

Every route, including `/healthz`, requires
`Authorization: Bearer $JAWNIX_SCRAPER_CONTROL_TOKEN`. The token must be at
least 32 characters and is never written to application logs.

## Local run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
CONTROL_DIR=../control \
KEYWORDS_PATH=../control/runtime/keywords.txt \
ACTIVE_STATES_PATH=../control/active_states.yaml \
SOURCE_SEGMENTS_PATH=../control/runtime/source_segments.yaml \
EXPORTS_DIR=../exports/by_state \
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/gmaps_pro \
JAWNIX_SCRAPER_CONTROL_TOKEN=local-development-token-at-least-32-chars \
.venv/bin/uvicorn app.main:app --reload
```

The production compose file binds port 8090 to the explicit
`SCRAPER_CONTROL_BIND_ADDRESS`, which must be the acquisition host's WireGuard
address. `DATABASE_URL_RO` should use the `gmaps_ro` role.
