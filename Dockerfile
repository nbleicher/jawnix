FROM postgres:18 AS postgres-client

# Compile the redesigned shell to content-hashed assets. Kept in its own stage
# so Node never reaches the runtime image.
FROM node:22-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# `npm run build` is `tsc -b && vite build`. The typecheck matters here because
# Vite only strips types, it never checks them — without `tsc` a type error
# bundles cleanly and ships.
#
# Measured peak RSS: ~388MB for tsc, ~237MB for vite (they run in sequence, so
# ~388MB is the high-water mark). The build host needs that much *available*,
# not merely installed. A host too short on free memory kills the build with a
# bare exit 137; a real type error exits 2.
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates postgresql-client restic \
  && rm -rf /var/lib/apt/lists/*

COPY --from=postgres-client /usr/lib/postgresql/18 /usr/lib/postgresql/18
RUN for tool in pg_dump pg_restore pg_basebackup psql pg_isready createdb dropdb; do \
      ln -sf "/usr/lib/postgresql/18/bin/$tool" "/usr/local/bin/$tool"; \
    done

COPY pyproject.toml ./
COPY jawnix ./jawnix
COPY jawnix_data ./jawnix_data
COPY config ./config
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

COPY admin.html login.html portal.html portal-accept.html theme.css config.example.js ./static/
COPY index.html app.py supabase-schema.sql ./legacy/

# Served by jawnix.frontend at /app when JAWNIX_ENABLE_NEW_UI is on. Present in
# the image either way, so enabling the flag needs no rebuild.
COPY --from=frontend-build /build/dist ./frontend/dist
COPY docker-entrypoint.sh ./
COPY ops ./ops
RUN chmod +x /app/docker-entrypoint.sh \
  && mkdir -p /srv/jawnix/batches

EXPOSE 8001

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "jawnix.api:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers"]
