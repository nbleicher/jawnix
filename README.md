# Jawnix VPS batch platform

Jawnix is a customer batch-request portal live at `https://jawnix.com`, backed by FastAPI and private PostgreSQL on the VPS. Supabase remains the identity provider; application data lives on the VPS. Telegram handles approval and Resend delivers an exact `phone,title` CSV from `Jawnix <hai@jawnix.com>`.

Billing, invoice, Stripe, and finance code remains in the repository for rollback, but the VPS application does not serve those routes and the new UI does not expose them. Keep `JAWNIX_ENABLE_BILLING=false`.

Customer feedback is intentionally lightweight: quality, positive-response,
and appointment milestones are retained with correction history and summarized
by Google Maps Source Segment.

## Services

- Caddy: public HTTP/HTTPS and static portal; proxies `/api`, `/admin/scraper`, and `/app` to FastAPI
- FastAPI: session, profile, request, admin, Telegram, and Resend APIs, plus the redesigned React shell at `/app`
- Worker: durable approval, allocation, Telegram, and delivery jobs
- PostgreSQL 18: private inventory and permanent allocation history
- Scheduler: nightly Google Maps dataset synchronization, Nightly Review, waiting-request retry, and 30-day CSV cleanup
- Backup worker: encrypted Restic PostgreSQL, WAL, and persistent Scraper Dataset backup with 14-day retention
- Cutover monitor: five-minute production health/service checks with Telegram state-change alerts

Only Caddy publishes ports. PostgreSQL, the API, worker, and schedulers remain on the Docker network.

## Redesigned UI

The rebuilt React interface lives in [`frontend/`](frontend/README.md) and is served by FastAPI at
`/app` behind `JAWNIX_ENABLE_NEW_UI`. Keep it `false` until the controlled cutover: while the flag is
off the prefix returns 404 and the current static pages are the only UI. The Docker image builds the
bundle in a separate Node stage and always ships it, so enabling the flag needs no rebuild.

Supabase Auth's allowed redirect URLs must include both
`$JAWNIX_PUBLIC_BASE_URL/portal-accept.html` and
`$JAWNIX_PUBLIC_BASE_URL/app/accept-invitation` during the transition. The
feature flag selects which address new invitations and password-recovery emails
use; keeping both allow-listed preserves rollback until the controlled cutover.

## Local verification

CI (`.github/workflows/ci.yml`) runs all of the following on every push and pull request, and
also builds the Docker image and smoke-tests both feature-flag states.

```sh
# Python. `.python-version` pins 3.12 to match the runtime image.
uv sync --extra test
uv run pytest

# Frontend. The shell integration tests skip without a build, so build first.
cd frontend && npm ci && npm run build && npm test && npm run test:e2e && cd ..

cp .env.example .env
# Replace every placeholder, then (Caddy renders /config.js from the
# environment — there is no file to generate):
docker compose config
docker compose up -d
curl https://jawnix.com/api/readyz
```

The PostgreSQL concurrency acceptance test is intentionally separate from unit tests:

```sh
docker run --rm --network jawnix_private \
  -e DATABASE_URL="$DATABASE_URL" \
  -v "$PWD:/src" -w /src jawnix-api \
  python scripts/postgres_concurrency_acceptance.py
```

## Data terminal

The worker and CLI call the same allocator.

```sh
python -m jawnix_data redistribute --request-id UUID
python -m jawnix_data sync-scrapers
python -m jawnix_data sync-scrapers --source NAME
python -m jawnix_data inventory --states TX,FL
python -m jawnix_data dry-run-allocation --agent-slug SLUG --quantity 100000 --states TX,FL
python -m jawnix_data retry-delivery --request-id UUID
```

`sync-scrapers` synchronizes the current committed Google Maps Scraper
Dataset Publication into PostgreSQL; it does not launch acquisition or modify
the dataset. It fails rather than importing an unversioned file.

Migration and production operations are documented in [OPERATIONS.md](docs/OPERATIONS.md). Copy `.env.example` for the complete secret/configuration contract. Never commit `.env` or generated `config.js`.

## Allocation rules

Allocation is all-or-nothing and transactionally locks rows with `SKIP LOCKED`. A phone is never repeated to an agent; members of one agency share permanent no-repeat history. Other recipients may receive it only after the seven-day global cooldown. Never-distributed rows sort first, followed by the oldest distribution date. A retry after generation reuses the same events and CSV.
