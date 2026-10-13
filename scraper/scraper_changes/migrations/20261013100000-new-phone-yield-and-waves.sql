-- +migrate Up
ALTER TABLE scrape_results ADD COLUMN IF NOT EXISTS new_phone_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scrape_results ADD COLUMN IF NOT EXISTS yield_verified BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE scrape_attempts ADD COLUMN IF NOT EXISTS duration_ms BIGINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS acquisition_phone_registry (
    phone TEXT PRIMARY KEY,
    first_job_id BIGINT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS acquisition_phone_registry_job ON acquisition_phone_registry(first_job_id)
    WHERE first_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS acquisition_phone_registry_recent ON acquisition_phone_registry(first_seen_at)
    WHERE first_job_id IS NOT NULL;

-- Prior discoveries establish the baseline. They are never counted as newly acquired.
INSERT INTO acquisition_phone_registry(phone)
SELECT DISTINCT CASE WHEN length(digits)=11 AND left(digits,1)='1' THEN right(digits,10) ELSE digits END
FROM (SELECT regexp_replace(COALESCE(phone,''),'[^0-9]','','g') AS digits FROM leads
      UNION ALL
      SELECT regexp_replace(COALESCE(phone,''),'[^0-9]','','g') FROM businesses) existing
WHERE length(digits)=10 OR (length(digits)=11 AND left(digits,1)='1')
ON CONFLICT DO NOTHING;

ALTER TABLE keyword_yield_evaluations ADD COLUMN IF NOT EXISTS coverage_cells JSONB;
ALTER TABLE keyword_yield_evaluations ADD COLUMN IF NOT EXISTS wave_cells JSONB;
ALTER TABLE keyword_yield_evaluations ADD COLUMN IF NOT EXISTS policy JSONB;
ALTER TABLE keyword_yield_evaluations ADD COLUMN IF NOT EXISTS new_phone_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE keyword_yield_evaluations ADD COLUMN IF NOT EXISTS new_phones_per_minute DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE keyword_yield_evaluations DROP CONSTRAINT IF EXISTS keyword_yield_evaluations_status_check;
ALTER TABLE keyword_yield_evaluations ADD CONSTRAINT keyword_yield_evaluations_status_check
    CHECK (status IN ('testing','accepted','expanding','completed','rejected','unverified','legacy'));
-- Preserve campaigns that already passed the old gate. New candidates use wave policy.
UPDATE keyword_yield_evaluations SET status='legacy' WHERE status='accepted' AND policy IS NULL;

-- +migrate Down
UPDATE keyword_yield_evaluations SET status='accepted' WHERE status IN ('expanding','completed');
ALTER TABLE keyword_yield_evaluations DROP CONSTRAINT keyword_yield_evaluations_status_check;
ALTER TABLE keyword_yield_evaluations ADD CONSTRAINT keyword_yield_evaluations_status_check
    CHECK (status IN ('testing','accepted','rejected','unverified','legacy'));
ALTER TABLE keyword_yield_evaluations DROP COLUMN new_phones_per_minute;
ALTER TABLE keyword_yield_evaluations DROP COLUMN new_phone_count;
ALTER TABLE keyword_yield_evaluations DROP COLUMN policy;
ALTER TABLE keyword_yield_evaluations DROP COLUMN wave_cells;
ALTER TABLE keyword_yield_evaluations DROP COLUMN coverage_cells;
DROP TABLE acquisition_phone_registry;
ALTER TABLE scrape_attempts DROP COLUMN duration_ms;
ALTER TABLE scrape_results DROP COLUMN yield_verified;
ALTER TABLE scrape_results DROP COLUMN new_phone_count;
