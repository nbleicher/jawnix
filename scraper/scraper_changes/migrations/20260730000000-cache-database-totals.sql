-- +migrate Up

CREATE MATERIALIZED VIEW IF NOT EXISTS database_totals_cache AS
SELECT 1::int AS singleton, now() AS refreshed_at, *
FROM (
WITH cleaned AS (
  SELECT CASE
    WHEN length(regexp_replace(COALESCE(phone,''), '\D', '', 'g')) = 10
      THEN regexp_replace(phone, '\D', '', 'g')
    WHEN length(regexp_replace(COALESCE(phone,''), '\D', '', 'g')) = 11
         AND left(regexp_replace(phone, '\D', '', 'g'), 1) = '1'
      THEN right(regexp_replace(phone, '\D', '', 'g'), 10)
  END AS normalized_phone
  FROM businesses
)
SELECT count(*) AS businesses,
       count(DISTINCT normalized_phone) FILTER (WHERE normalized_phone IS NOT NULL) AS unique_phones
FROM cleaned
) AS totals;

CREATE UNIQUE INDEX IF NOT EXISTS database_totals_cache_pkey
  ON database_totals_cache (singleton);

CREATE MATERIALIZED VIEW IF NOT EXISTS database_state_summaries_cache AS
SELECT now() AS refreshed_at, *
FROM (
WITH cleaned AS (
  SELECT upper(trim(state)) AS state,
         CASE
           WHEN length(regexp_replace(COALESCE(phone,''), '\D', '', 'g')) = 10
             THEN regexp_replace(phone, '\D', '', 'g')
           WHEN length(regexp_replace(COALESCE(phone,''), '\D', '', 'g')) = 11
                AND left(regexp_replace(phone, '\D', '', 'g'), 1) = '1'
             THEN right(regexp_replace(phone, '\D', '', 'g'), 10)
         END AS normalized_phone,
         COALESCE(NULLIF(lower(trim(keyword)), ''), '__uncategorized__') AS niche_key
  FROM businesses
  WHERE trim(COALESCE(state,'')) ~* '^[a-z]{2}$'
)
SELECT state, count(*) AS businesses,
       count(DISTINCT normalized_phone) FILTER (WHERE normalized_phone IS NOT NULL) AS unique_phones,
       count(DISTINCT niche_key) AS niches
FROM cleaned
GROUP BY state
) AS summaries;

CREATE UNIQUE INDEX IF NOT EXISTS database_state_summaries_cache_pkey
  ON database_state_summaries_cache (state);

-- +migrate Down
DROP MATERIALIZED VIEW IF EXISTS database_state_summaries_cache;
DROP MATERIALIZED VIEW IF EXISTS database_totals_cache;
