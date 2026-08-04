DASHBOARD_STATS = """
SELECT
  (SELECT count(*) FROM businesses) AS businesses,
  (SELECT count(*) FROM businesses WHERE COALESCE(phone,'')<>'') AS phone_businesses,
  (SELECT count(DISTINCT phone) FROM businesses WHERE COALESCE(phone,'')<>'') AS unique_phones,
  (SELECT count(*) FROM leads) AS leads,
  (SELECT count(*) FROM available_leads) AS available_leads,
  (SELECT count(*) FROM river_job WHERE kind='scrape' AND state IN ('available','pending','scheduled','retryable')) AS queue_depth,
  (SELECT count(*) FROM river_job WHERE kind='scrape' AND state='running') AS running_jobs,
  (SELECT count(*) FROM river_job WHERE kind='scrape' AND state='retryable') AS retryable_jobs,
  COALESCE((SELECT extract(epoch FROM now()-min(created_at))::bigint FROM river_job WHERE kind='scrape' AND state IN ('available','pending','retryable')),0) AS oldest_queue_secs,
  (SELECT count(*) FROM businesses WHERE first_seen >= now() - interval '1 hour') AS added_last_hour,
  COALESCE((SELECT avg((result_count = 0)::int)::float FROM scrape_results WHERE created_at >= now() - interval '1 hour'), 0) AS empty_rate
"""

CURRENT_WORKERS = """
SELECT w.box_id, w.container_name, w.reported_at, w.active_jobs, w.jobs_processed,
       w.results_per_min, w.status, active_job.current_state, active_job.current_keyword,
       active_job.current_job_id
FROM worker_heartbeats w
LEFT JOIN LATERAL (
  SELECT upper(j.args->>'state') AS current_state,
         j.args->>'keyword' AS current_keyword,
         j.id AS current_job_id
  FROM river_job j
  WHERE j.kind='scrape' AND j.state='running' AND w.container_id IS NOT NULL
    AND j.attempted_by[array_length(j.attempted_by, 1)] LIKE w.container_id || '_%'
  ORDER BY j.attempted_at DESC NULLS LAST
  LIMIT 1
) active_job ON TRUE
ORDER BY w.box_id, w.container_name
"""

PIPELINE_ACTIVITY = """
WITH queue AS (
  SELECT count(*) FILTER (WHERE kind='scrape' AND state IN ('available','pending','scheduled','retryable')) AS queue_depth,
         count(*) FILTER (WHERE kind='scrape' AND state='running') AS running_jobs,
         count(*) FILTER (WHERE kind='scrape' AND state='retryable') AS retryable_jobs
  FROM river_job
), recent AS (
  SELECT count(*) FILTER (WHERE created_at >= now()-interval '1 minute') AS jobs_last_minute,
         count(*) FILTER (WHERE created_at >= now()-interval '5 minutes') AS jobs_last_five_minutes,
         COALESCE(sum(result_count) FILTER (WHERE created_at >= now()-interval '1 minute'), 0) AS results_last_minute,
         max(created_at) AS latest_result_at
  FROM scrape_results
), touched AS (
  SELECT count(*) FILTER (WHERE last_seen >= now()-interval '1 minute') AS businesses_last_minute,
         count(*) FILTER (WHERE last_seen >= now()-interval '5 minutes') AS businesses_last_five_minutes,
         count(*) AS businesses_total,
         max(last_seen) AS latest_business_at
  FROM businesses
), latest AS (
  SELECT COALESCE(NULLIF(sr.keyword,''), NULLIF(j.args->>'keyword',''), 'unknown') AS latest_keyword,
         COALESCE(upper(j.args->>'state'), 'UNKNOWN') AS latest_state,
         result_count AS latest_result_count, sr.created_at AS latest_job_at
  FROM scrape_results sr LEFT JOIN river_job j ON j.id=sr.job_id
  ORDER BY sr.created_at DESC LIMIT 1
), fleet AS (
  SELECT count(*) FILTER (
    WHERE reported_at >= now()-interval '3 minutes' AND status IN ('alive','ok')
  ) AS healthy_workers
  FROM worker_heartbeats
)
SELECT queue.*, recent.*, touched.*, latest.*, fleet.*,
       GREATEST(recent.latest_result_at, touched.latest_business_at) AS latest_write_at
FROM queue CROSS JOIN recent CROSS JOIN touched CROSS JOIN fleet LEFT JOIN latest ON TRUE
"""

PIPELINE_LOG = """
SELECT * FROM (
  SELECT sr.job_id, sr.created_at,
         COALESCE(NULLIF(sr.keyword,''), NULLIF(j.args->>'keyword',''), 'unknown') AS keyword,
         COALESCE(upper(j.args->>'state'), 'UNKNOWN') AS state,
         sr.result_count, sr.phone_count
  FROM scrape_results sr
  LEFT JOIN river_job j ON j.id=sr.job_id
  WHERE sr.phone_count > 0
  ORDER BY sr.created_at DESC, sr.job_id DESC
  LIMIT 14
) events
ORDER BY created_at, job_id
"""

CANCEL_PENDING_SCRAPE_JOBS = """
WITH cancelled AS (
  UPDATE river_job
  SET state='cancelled', finalized_at=now()
  WHERE kind='scrape' AND state IN ('available','pending','scheduled','retryable')
  RETURNING id
)
SELECT count(*)::int FROM cancelled
"""

STACK_LATEST = """
SELECT * FROM stack_samples ORDER BY captured_at DESC LIMIT 1
"""

STACK_TRENDS = """
SELECT captured_at, cpu_percent, memory_used_bytes, memory_total_bytes,
       queue_depth, businesses_total, completed_jobs_total
FROM stack_samples
WHERE captured_at >= now() - interval '25 hours'
ORDER BY captured_at
"""

ALERT_EVENTS = """
SELECT checked_at, status, messages
FROM pipeline_alert_events
WHERE checked_at >= now() - interval '24 hours'
ORDER BY checked_at DESC LIMIT 12
"""

TOP_STATES = """
SELECT upper(COALESCE(state, 'unknown')) AS state, count(*) AS businesses
FROM businesses GROUP BY state ORDER BY businesses DESC LIMIT 10
"""

STATE_AGGREGATES = """
WITH business_counts AS (
  SELECT lower(state) AS state, count(*) AS businesses FROM businesses GROUP BY lower(state)
), posted AS (
  SELECT lower(state) AS state, count(DISTINCT cell) AS posted_cells,
         count(DISTINCT keyword) AS active_keywords, max(updated_at) AS last_enqueued
  FROM enqueue_log WHERE day=CURRENT_DATE AND status='posted' GROUP BY lower(state)
)
SELECT COALESCE(b.state,p.state) AS state, COALESCE(b.businesses,0) AS businesses,
       COALESCE(p.posted_cells,0) AS posted_cells, COALESCE(p.active_keywords,0) AS active_keywords,
       p.last_enqueued
FROM business_counts b FULL OUTER JOIN posted p USING (state)
"""

STATE_KEYWORDS = """
WITH keys AS (
  SELECT keyword FROM businesses WHERE lower(state)=$1
  UNION SELECT keyword FROM enqueue_log WHERE lower(state)=$1
), b AS (
  SELECT keyword, count(*) AS businesses FROM businesses WHERE lower(state)=$1 GROUP BY keyword
), e AS (
  SELECT keyword, count(DISTINCT cell) FILTER (WHERE status='posted') AS posted_cells,
         max(updated_at) AS last_enqueued
  FROM enqueue_log WHERE lower(state)=$1 AND day=CURRENT_DATE GROUP BY keyword
), r AS (
  SELECT sr.keyword, avg((sr.result_count=0)::int)::float AS empty_rate
  FROM scrape_results sr JOIN river_job j ON j.id=sr.job_id
  WHERE lower(j.args->>'state')=$1 AND sr.created_at >= now() - interval '24 hours'
  GROUP BY sr.keyword
)
SELECT k.keyword, COALESCE(b.businesses,0) AS businesses, COALESCE(e.posted_cells,0) AS posted_cells,
       e.last_enqueued, COALESCE(r.empty_rate,0) AS empty_rate
FROM keys k LEFT JOIN b USING(keyword) LEFT JOIN e USING(keyword) LEFT JOIN r USING(keyword)
ORDER BY k.keyword
"""

STATE_CELL_STATUS = """
SELECT cell, CASE
  WHEN bool_or(status='posted') THEN 'posted'
  WHEN bool_or(status='reserved') THEN 'reserved'
  WHEN bool_or(status='failed') THEN 'failed'
  ELSE 'uncovered' END AS status
FROM enqueue_log WHERE lower(state)=$1 AND day=CURRENT_DATE GROUP BY cell
"""

HISTORY_BASE = """
SELECT h.keyword, upper(h.state) AS state, h.last_enqueued,
       count(DISTINCT e.cell) FILTER (WHERE e.status='posted') AS cells_posted,
       min(e.enqueued_at) AS first_enqueued, max(e.updated_at) AS latest_enqueued
FROM keyword_history h LEFT JOIN enqueue_log e
  ON e.keyword=h.keyword AND e.state=h.state
WHERE ($1='' OR h.keyword ILIKE '%' || $1 || '%') AND ($2='' OR lower(h.state)=$2)
GROUP BY h.keyword,h.state,h.last_enqueued
"""

PHONE_MATRIX = """
SELECT upper(COALESCE(state,'unknown')) AS state, COALESCE(keyword,'unknown') AS keyword,
       count(DISTINCT phone) AS phones
FROM businesses WHERE COALESCE(phone,'')<>''
GROUP BY state,keyword ORDER BY state,phones DESC
"""

PHONE_TOTALS = """
SELECT upper(COALESCE(state,'unknown')) AS state, count(DISTINCT phone) AS phones
FROM businesses WHERE COALESCE(phone,'')<>'' GROUP BY state ORDER BY state
"""

DATABASE_STATE_SUMMARIES = """
SELECT state, businesses, unique_phones, niches
FROM database_state_summaries_cache
ORDER BY businesses DESC, state
"""

DATABASE_TOTALS = """
SELECT businesses, unique_phones
FROM database_totals_cache
"""

DATABASE_STATE_EXISTS = """
SELECT EXISTS (
  SELECT 1 FROM businesses WHERE lower(trim(state))=$1
)
"""

DATABASE_STATE_NICHES = """
WITH cleaned AS (
  SELECT COALESCE(NULLIF(lower(trim(keyword)), ''), '__uncategorized__') AS niche_key,
         CASE
           WHEN length(regexp_replace(COALESCE(phone,''), '\\D', '', 'g')) = 10
             THEN regexp_replace(phone, '\\D', '', 'g')
           WHEN length(regexp_replace(COALESCE(phone,''), '\\D', '', 'g')) = 11
                AND left(regexp_replace(phone, '\\D', '', 'g'), 1) = '1'
             THEN right(regexp_replace(phone, '\\D', '', 'g'), 10)
         END AS normalized_phone
  FROM businesses
  WHERE lower(trim(state))=$1
)
SELECT niche_key,
       CASE WHEN niche_key='__uncategorized__' THEN 'Uncategorized' ELSE niche_key END AS niche,
       count(*) AS businesses,
       count(DISTINCT normalized_phone) FILTER (WHERE normalized_phone IS NOT NULL) AS unique_phones
FROM cleaned
GROUP BY niche_key
ORDER BY unique_phones DESC, businesses DESC, niche
"""

DATABASE_EXPORT = """
WITH cleaned AS (
  SELECT id, COALESCE(title, '') AS business_name, upper(trim(state)) AS state,
         COALESCE(NULLIF(lower(trim(keyword)), ''), '__uncategorized__') AS niche_key,
         last_seen,
         CASE
           WHEN length(regexp_replace(COALESCE(phone,''), '\\D', '', 'g')) = 10
             THEN regexp_replace(phone, '\\D', '', 'g')
           WHEN length(regexp_replace(COALESCE(phone,''), '\\D', '', 'g')) = 11
                AND left(regexp_replace(phone, '\\D', '', 'g'), 1) = '1'
             THEN right(regexp_replace(phone, '\\D', '', 'g'), 10)
         END AS phone_number
  FROM businesses
  WHERE lower(trim(state))=$1
), ranked AS (
  SELECT business_name, phone_number, state,
         row_number() OVER (
           PARTITION BY phone_number ORDER BY last_seen DESC, id DESC
         ) AS rank
  FROM cleaned
  WHERE phone_number IS NOT NULL
    AND ($2::text[] IS NULL OR niche_key = ANY($2::text[]))
)
SELECT business_name, phone_number, state
FROM ranked
WHERE rank=1
ORDER BY business_name, phone_number
"""

LEAD_STATS = """
SELECT (SELECT count(*) FROM leads) AS total,
       (SELECT count(*) FROM available_leads) AS available
"""

BROWSE_BUSINESSES = """
SELECT id,title,phone,website,upper(COALESCE(state,'')) AS state,keyword,last_seen
FROM businesses
WHERE ($1='' OR title ILIKE '%' || $1 || '%' OR phone ILIKE '%' || $1 || '%' OR website ILIKE '%' || $1 || '%')
  AND ($2='' OR lower(state)=$2)
ORDER BY last_seen DESC, id DESC LIMIT $3 OFFSET $4
"""

BROWSE_COUNT = """
SELECT count(*) FROM businesses
WHERE ($1='' OR title ILIKE '%' || $1 || '%' OR phone ILIKE '%' || $1 || '%' OR website ILIKE '%' || $1 || '%')
  AND ($2='' OR lower(state)=$2)
"""

USED_KEYWORDS = """
SELECT keyword FROM (
  SELECT lower(trim(keyword)) AS keyword FROM enqueue_log
  UNION SELECT lower(trim(keyword)) FROM keyword_history
  UNION SELECT lower(trim(keyword)) FROM businesses
) used
WHERE COALESCE(keyword, '') <> ''
ORDER BY keyword
"""

KEYWORD_WINNERS = """
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
ORDER BY phones_per_cell DESC, phone_businesses DESC, keyword
LIMIT 50
"""

KEYWORD_DRAFT = """
SELECT id, created_at, mode, seed_keyword, model, keywords, excluded_count, accepted_at
FROM keyword_generations
WHERE id=$1::uuid AND created_at >= NOW()-($2::text || ' hours')::interval
"""

INSERT_KEYWORD_GENERATION = """
INSERT INTO keyword_generations
  (id, mode, seed_keyword, model, keywords, excluded_count)
VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
RETURNING id
"""

ACCEPT_KEYWORD_GENERATION = """
UPDATE keyword_generations SET accepted_at=COALESCE(accepted_at, NOW())
WHERE id=$1::uuid
RETURNING id
"""

CLEANUP_KEYWORD_GENERATIONS = """
DELETE FROM keyword_generations WHERE created_at < NOW()-interval '90 days' RETURNING id
"""

AUTO_KEYWORD_ROLLOVER_STATE = """
SELECT
  COALESCE((SELECT lower(value) IN ('1','true','yes','on')
            FROM app_config WHERE key='auto_keyword_rollover'), false) AS enabled,
  (SELECT status FROM keyword_rollover_events ORDER BY created_at DESC LIMIT 1) AS last_status,
  (SELECT created_at FROM keyword_rollover_events ORDER BY created_at DESC LIMIT 1) AS last_event_at,
  (SELECT message FROM keyword_rollover_events ORDER BY created_at DESC LIMIT 1) AS last_message
"""

AUTO_KEYWORD_ROLLOVER_PROGRESS = """
SELECT
  (SELECT count(DISTINCT (lower(keyword),lower(state),cell))
   FROM enqueue_log
   WHERE lower(keyword)=ANY($1::text[]) AND lower(state)=ANY($2::text[])
     AND day >= $3::date AND status='posted') AS posted_jobs,
  (SELECT count(*) FROM keyword_history
   WHERE lower(keyword)=ANY($1::text[]) AND lower(state)=ANY($2::text[])
     AND last_enqueued >= $3::date) AS completed_pairs,
  (SELECT count(*) FROM river_job
   WHERE kind='scrape' AND state IN ('available','pending','scheduled','retryable','running')
     AND lower(args->>'keyword')=ANY($1::text[])
     AND lower(args->>'state')=ANY($2::text[])) AS active_jobs
"""

SET_AUTO_KEYWORD_ROLLOVER = """
INSERT INTO app_config (key,value,encrypted,updated_at)
VALUES ('auto_keyword_rollover',$1,false,NOW())
ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()
RETURNING value
"""

INSERT_KEYWORD_ROLLOVER_EVENT = """
INSERT INTO keyword_rollover_events (status,previous_keywords,next_keywords,message)
VALUES ($1,$2::jsonb,$3::jsonb,$4)
RETURNING id
"""
