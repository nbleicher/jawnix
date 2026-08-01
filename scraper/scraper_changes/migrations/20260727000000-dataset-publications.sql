-- +migrate Up
CREATE TABLE IF NOT EXISTS scraper_dataset_publications (
    publication_date DATE PRIMARY KEY,
    committed_at TIMESTAMPTZ NOT NULL,
    business_count BIGINT NOT NULL,
    lead_count BIGINT NOT NULL,
    latest_job_at TIMESTAMPTZ NOT NULL,
    checksum TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS scraper_dataset_publications_committed_at_idx
    ON scraper_dataset_publications (committed_at DESC);

-- +migrate Down
DROP TABLE IF EXISTS scraper_dataset_publications;
