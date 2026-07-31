-- Minimal upstream gmapssaas control-plane schema used by the dashboard tests.
-- The scale migrations are applied on top of these owned upstream tables by
-- tests/conftest.py, so a fresh PostgreSQL database exercises the real
-- migration files rather than a hand-maintained copy of their final shape.

CREATE TABLE IF NOT EXISTS river_job (
    id            BIGINT PRIMARY KEY,
    state         TEXT NOT NULL,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    args          JSONB NOT NULL DEFAULT '{}'::jsonb,
    kind          TEXT NOT NULL,
    attempted_by  TEXT[],
    attempted_at  TIMESTAMPTZ,
    finalized_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    encrypted   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
