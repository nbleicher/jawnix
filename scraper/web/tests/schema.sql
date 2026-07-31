DO $$
BEGIN
  IF to_regclass('public.businesses') IS NULL
     OR to_regclass('public.leads') IS NULL
     OR to_regclass('public.enqueue_log') IS NULL
     OR to_regclass('public.keyword_history') IS NULL
     OR to_regclass('public.scrape_results') IS NULL
     OR to_regclass('public.worker_heartbeats') IS NULL
     OR to_regclass('public.stack_samples') IS NULL
     OR to_regclass('public.pipeline_alert_events') IS NULL
     OR to_regclass('public.keyword_generations') IS NULL
     OR to_regclass('public.river_job') IS NULL
     OR to_regclass('public.available_leads') IS NULL THEN
    RAISE EXCEPTION 'test database is missing required migrations';
  END IF;
END $$;
