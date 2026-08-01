-- +migrate Up
-- Lightweight per-job result counts for the spool-and-ship path.
--
-- Raw business payloads stay in archived NDJSON files; this table exists so
-- queue stats, empty-rate alerts, and job-level counts still have a stable
-- source without storing a large JSONB blob per job.
CREATE TABLE IF NOT EXISTS scrape_results (
    job_id       BIGINT PRIMARY KEY,
    keyword      TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

ALTER TABLE scrape_results ADD COLUMN IF NOT EXISTS keyword TEXT;
ALTER TABLE scrape_results ADD COLUMN IF NOT EXISTS result_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scrape_results ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC');
CREATE INDEX IF NOT EXISTS idx_scrape_results_created_at ON scrape_results (created_at);

-- Older scraper migrations may have created this as a raw JSONB landing table.
-- The spool path inserts only count metadata, so legacy raw columns must be
-- optional if they exist.
-- +migrate StatementBegin
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'scrape_results' AND column_name = 'results'
    ) THEN
        ALTER TABLE scrape_results ALTER COLUMN results DROP NOT NULL;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'scrape_results' AND column_name = 'keyword'
    ) THEN
        ALTER TABLE scrape_results ALTER COLUMN keyword DROP NOT NULL;
    END IF;
END $$;
-- +migrate StatementEnd

-- +migrate Down
-- Do not drop scrape_results on rollback; older scraper builds may own the same
-- table and may have stored raw JSONB results there.
DROP INDEX IF EXISTS idx_scrape_results_created_at;
