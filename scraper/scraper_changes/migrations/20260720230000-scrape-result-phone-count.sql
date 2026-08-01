-- +migrate Up
ALTER TABLE scrape_results
  ADD COLUMN IF NOT EXISTS phone_count INTEGER NOT NULL DEFAULT 0;

UPDATE scrape_results sr
SET phone_count = counts.phone_count
FROM (
  SELECT source_job_id, count(*)::integer AS phone_count
  FROM businesses
  WHERE source_job_id IS NOT NULL AND btrim(COALESCE(phone, '')) <> ''
  GROUP BY source_job_id
) counts
WHERE sr.job_id = counts.source_job_id AND sr.phone_count = 0;

CREATE INDEX IF NOT EXISTS idx_scrape_results_phone_activity
  ON scrape_results (created_at DESC) WHERE phone_count > 0;

-- +migrate Down
DROP INDEX IF EXISTS idx_scrape_results_phone_activity;
ALTER TABLE scrape_results DROP COLUMN IF EXISTS phone_count;
