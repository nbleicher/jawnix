-- +migrate Up
CREATE TABLE IF NOT EXISTS keyword_generations (
    id                 UUID PRIMARY KEY,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mode               TEXT NOT NULL,
    seed_keyword       TEXT,
    model              TEXT NOT NULL,
    keywords           JSONB NOT NULL,
    excluded_count     INTEGER NOT NULL DEFAULT 0,
    accepted_at        TIMESTAMPTZ,
    CONSTRAINT keyword_generations_mode_check CHECK (mode IN ('broad', 'adjacent')),
    CONSTRAINT keyword_generations_keywords_array_check CHECK (jsonb_typeof(keywords) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_keyword_generations_created_at
    ON keyword_generations (created_at DESC);

-- +migrate Down
DROP TABLE IF EXISTS keyword_generations;
