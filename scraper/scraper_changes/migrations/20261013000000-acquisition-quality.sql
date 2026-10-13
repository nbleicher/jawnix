-- +migrate Up
CREATE TABLE IF NOT EXISTS scrape_attempts (
    job_id BIGINT NOT NULL,
    attempt INTEGER NOT NULL,
    keyword TEXT NOT NULL,
    state TEXT NOT NULL,
    cell TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success','empty','blocked','fetch_error','parse_error','timeout')),
    result_count INTEGER NOT NULL DEFAULT 0,
    phone_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, attempt)
);
CREATE INDEX IF NOT EXISTS scrape_attempts_campaign ON scrape_attempts (lower(keyword), lower(state), cell, created_at DESC);
CREATE INDEX IF NOT EXISTS scrape_attempts_recent ON scrape_attempts (created_at DESC);

CREATE TABLE IF NOT EXISTS acquisition_health (
    id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    failure_streak INTEGER NOT NULL DEFAULT 0,
    empty_streak INTEGER NOT NULL DEFAULT 0,
    probe_after TIMESTAMPTZ,
    lease_until TIMESTAMPTZ,
    blocked_until TIMESTAMPTZ,
    last_probe_at TIMESTAMPTZ,
    last_probe_outcome TEXT,
    cooldown_level INTEGER NOT NULL DEFAULT 0,
    known_good JSONB
);
INSERT INTO acquisition_health (id) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS keyword_yield_evaluations (
    keyword TEXT NOT NULL,
    state TEXT NOT NULL,
    started_on DATE NOT NULL,
    sample_cells JSONB NOT NULL,
    full_cells INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('testing','accepted','rejected','unverified','legacy')),
    completed_cells INTEGER NOT NULL DEFAULT 0,
    productive_cells INTEGER NOT NULL DEFAULT 0,
    phone_count INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (keyword, state, started_on)
);

-- Classified metrics deliberately exclude historical, ambiguous zero-result jobs.
CREATE OR REPLACE VIEW acquisition_quality_1h AS
WITH latest AS (
    SELECT DISTINCT ON (job_id) * FROM scrape_attempts
    WHERE created_at >= now() - interval '1 hour'
    ORDER BY job_id, attempt DESC
)
SELECT count(*) AS jobs,
       count(*) FILTER (WHERE outcome='empty') AS empty_jobs,
       count(*) FILTER (WHERE outcome NOT IN ('success','empty')) AS failed_jobs,
       COALESCE(avg((outcome='empty')::int) FILTER (WHERE outcome IN ('success','empty')),0)::float AS empty_rate
FROM latest;

-- +migrate Down
DROP VIEW acquisition_quality_1h;
DROP TABLE keyword_yield_evaluations;
DROP TABLE acquisition_health;
DROP TABLE scrape_attempts;
