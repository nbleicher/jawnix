-- +migrate Up

-- Rank the full acquisition history in the existing cache refresh job,
-- instead of sorting millions of enqueue rows in an interactive request.
CREATE MATERIALIZED VIEW IF NOT EXISTS keyword_winners_cache AS
SELECT now() AS refreshed_at, rankings.*
FROM (
WITH coverage AS (
  SELECT lower(trim(keyword)) AS keyword,
         count(DISTINCT (lower(state), cell)) FILTER (WHERE status='posted') AS posted_cells,
         max(updated_at) AS last_used
  FROM enqueue_log
  WHERE COALESCE(trim(keyword), '') <> ''
  GROUP BY lower(trim(keyword))
), business_counts AS (
  SELECT lower(trim(keyword)) AS keyword,
         count(*) AS businesses,
         count(*) FILTER (WHERE COALESCE(phone, '') <> '') AS phone_businesses
  FROM businesses
  WHERE COALESCE(trim(keyword), '') <> ''
  GROUP BY lower(trim(keyword))
)
SELECT c.keyword, COALESCE(b.businesses, 0) AS businesses,
       COALESCE(b.phone_businesses, 0) AS phone_businesses,
       c.posted_cells, c.last_used,
       COALESCE(b.phone_businesses::float / NULLIF(c.posted_cells, 0), 0) AS phones_per_cell,
       COALESCE(b.phone_businesses::float / NULLIF(b.businesses, 0), 0) AS phone_rate
FROM coverage c LEFT JOIN business_counts b USING (keyword)
WHERE c.posted_cells >= 100
) AS rankings;

CREATE UNIQUE INDEX IF NOT EXISTS keyword_winners_cache_pkey
  ON keyword_winners_cache (keyword);

-- +migrate Down
DROP MATERIALIZED VIEW IF EXISTS keyword_winners_cache;
