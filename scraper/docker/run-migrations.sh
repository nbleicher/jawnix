#!/bin/sh
set -eu
export GOBIN=/tmp/bin
go install github.com/rubenv/sql-migrate/sql-migrate@v1.8.0
cat >/tmp/scraper.yml <<EOF
production:
  dialect: postgres
  datasource: ${DATABASE_URL}
  dir: /scraper/migrations
  table: scraper_migrations
EOF
cat >/tmp/scale.yml <<EOF
production:
  dialect: postgres
  datasource: ${DATABASE_URL}
  dir: /scale-migrations
  table: scale_migrations
EOF
/tmp/bin/sql-migrate up -config=/tmp/scraper.yml -env=production
/tmp/bin/sql-migrate up -config=/tmp/scale.yml -env=production
