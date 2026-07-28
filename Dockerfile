FROM postgres:18 AS postgres-client

# Compile the redesigned shell to content-hashed assets. Kept in its own stage
# so Node never reaches the runtime image.
FROM node:22-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Bundle only. `npm run build` also runs `tsc -b`, which needs more memory than a
# modest build host has (it is OOM-killed under ~2GB).
#
# NOTE: this repo has no CI, so nothing currently enforces `npm run typecheck`
# before an image is built. Until a check exists, a type error can reach an
# image. See for-noah-review.md.
RUN npm run build:bundle

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
