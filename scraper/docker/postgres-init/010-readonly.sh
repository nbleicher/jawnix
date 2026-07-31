#!/usr/bin/env bash
set -euo pipefail

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gmaps_ro') THEN
    CREATE ROLE gmaps_ro LOGIN PASSWORD '${GMAPS_RO_PASSWORD}';
  ELSE
    ALTER ROLE gmaps_ro PASSWORD '${GMAPS_RO_PASSWORD}';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO gmaps_ro;
GRANT USAGE ON SCHEMA public TO gmaps_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO gmaps_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO gmaps_ro;
SQL
