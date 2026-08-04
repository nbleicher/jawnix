#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3.12}

if [ -z "${TEST_DATABASE_URL:-}" ]; then
    echo "TEST_DATABASE_URL must point to an isolated PostgreSQL database." >&2
    exit 2
fi

for command in "$PYTHON_BIN" npm docker git; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command is unavailable: $command" >&2
        exit 2
    fi
done

required_files="
.dockerignore
.env.box.example
docker-compose.box.yml
docker/postgres-init/010-readonly.sh
docker/run-migrations.sh
scripts/fetch-scraper-source.sh
scraper_changes/worker-build.patch
control/alert.py
control/enqueue.py
control/export_leads.py
control/publish_dataset.py
control/refresh_database_cache.py
control/uptime_probe.py
worker/heartbeat.py
worker/shipper.py
systemd/gms-alert.service
systemd/gms-database-cache.service
systemd/gms-database-cache.timer
systemd/gms-dataset-publication.service
systemd/gms-dataset-publication.timer
systemd/gms-heartbeat.service
systemd/gms-uptime.service
systemd/gms-uptime.timer
web/Dockerfile
web/app/main.py
web/tests/upstream_schema.sql
"

for path in $required_files; do
    if ! git -C "$ROOT" ls-files --error-unmatch "$path" >/dev/null 2>&1; then
        echo "Required deployable source is not version-controlled: $path" >&2
        exit 1
    fi
done

temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/gms-baseline.XXXXXX")
cleanup() {
    rm -rf "$temp_dir"
}
trap cleanup EXIT INT TERM

"$PYTHON_BIN" -m venv "$temp_dir/venv"
"$temp_dir/venv/bin/pip" install -q -r "$ROOT/web/requirements-dev.txt"

database_name=$(
    TEST_DATABASE_URL="$TEST_DATABASE_URL" \
        "$temp_dir/venv/bin/python" -c \
        'import os, psycopg2; connection=psycopg2.connect(os.environ["TEST_DATABASE_URL"], connect_timeout=5); cursor=connection.cursor(); cursor.execute("SELECT current_database()"); print(cursor.fetchone()[0]); connection.close()'
)
if [ "$database_name" != "gms_baseline_test" ]; then
    echo "Refusing to reset database '$database_name'; expected gms_baseline_test." >&2
    exit 2
fi

"$ROOT/scripts/fetch-scraper-source.sh" "$temp_dir/scraper-src"

(
    cd "$ROOT/web"
    npm ci --ignore-scripts
    npm run build:css
    TEST_DATABASE_URL="$TEST_DATABASE_URL" \
        PYTHONPATH="$ROOT/web:$ROOT" \
        "$temp_dir/venv/bin/pytest" -q
)

git -C "$ROOT" diff --exit-code -- web/app/static/app.css

POSTGRES_PASSWORD=baseline-check \
GMAPS_RO_PASSWORD=baseline-readonly \
HASHID_SALT=baseline-hash \
JAWNIX_SCRAPER_CONTROL_TOKEN=baseline-scraper-control-token-0000000000000000 \
SCRAPER_CONTROL_BIND_ADDRESS=127.0.0.1 \
SCRAPER_SRC="$temp_dir/scraper-src" \
docker compose -f "$ROOT/docker-compose.box.yml" config --quiet

POSTGRES_PASSWORD=baseline-check \
GMAPS_RO_PASSWORD=baseline-readonly \
HASHID_SALT=baseline-hash \
JAWNIX_SCRAPER_CONTROL_TOKEN=baseline-scraper-control-token-0000000000000000 \
SCRAPER_CONTROL_BIND_ADDRESS=127.0.0.1 \
SCRAPER_SRC="$temp_dir/scraper-src" \
GMS_GO_BUILD_FLAGS="-p=1 -gcflags=all=-l" \
GMS_GOGC=10 \
docker compose -f "$ROOT/docker-compose.box.yml" build scraper-control worker

echo "Scraper source baseline verified."
