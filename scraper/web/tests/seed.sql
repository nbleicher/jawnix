TRUNCATE businesses, leads, enqueue_log, keyword_history, scrape_results, worker_heartbeats,
         stack_samples, pipeline_alert_events, river_job RESTART IDENTITY CASCADE;
TRUNCATE keyword_rollover_events, keyword_generations RESTART IDENTITY;
INSERT INTO app_config (key,value,encrypted,updated_at)
VALUES ('auto_keyword_rollover','false',false,NOW())
ON CONFLICT (key) DO UPDATE SET value='false', updated_at=NOW();

INSERT INTO businesses (dedup_key, place_id, title, phone, website, state, keyword, cell, last_seen) VALUES
  ('pid:1','1','Buckeye Plumbing','614-555-0101','https://buckeye.example','oh','plumbers','39.1,-82.1',NOW()),
  ('pid:2','2','Capital Electric','614-555-0101','https://capital.example','oh','electricians','39.2,-82.2',NOW()),
  ('pid:3','3','North Plumbing','216-555-0102',NULL,'oh','plumbers','40.2,-81.9',NOW()),
  ('pid:4','4','Bluegrass Plumbing','502-555-0103',NULL,'ky','plumbers','38.1,-84.5',NOW()),
  ('pid:5','5','No Phone LLC','',NULL,'mo','roofers','38.6,-92.3',NOW());

INSERT INTO leads (phone,title,state,flow,first_seen) VALUES
  ('6145550101','Buckeye Plumbing','OH','global_combine',CURRENT_DATE),
  ('2165550102','North Plumbing','OH','global_combine',CURRENT_DATE),
  ('5025550103','Bluegrass Plumbing','KY','global_combine',CURRENT_DATE);

INSERT INTO enqueue_log (keyword,state,cell,day,status,enqueued_at,updated_at,last_error) VALUES
  ('plumbers','oh','38.512289,-84.678438',CURRENT_DATE,'posted',NOW()-interval '2 hours',NOW()-interval '2 hours',NULL),
  ('plumbers','oh','38.512289,-84.391395',CURRENT_DATE,'posted',NOW()-interval '2 hours',NOW()-interval '1 hour',NULL),
  ('electricians','oh','38.736867,-84.678438',CURRENT_DATE,'reserved',NOW()-interval '10 minutes',NOW()-interval '10 minutes',NULL),
  ('roofers','oh','38.736867,-84.391395',CURRENT_DATE,'failed',NOW()-interval '20 minutes',NOW()-interval '20 minutes','timeout'),
  ('plumbers','ky','36.612289,-89.426543',CURRENT_DATE,'posted',NOW()-interval '1 hour',NOW()-interval '1 hour',NULL);

INSERT INTO enqueue_log (keyword,state,cell,day,status,enqueued_at,updated_at,last_error)
SELECT 'plumbers','oh','seed-cell-' || value,CURRENT_DATE,'posted',NOW()-interval '1 hour',NOW(),NULL
FROM generate_series(1,100) value;

INSERT INTO keyword_history (keyword,state,last_enqueued) VALUES
  ('plumbers','oh',CURRENT_DATE),('electricians','oh',CURRENT_DATE-1),('plumbers','ky',CURRENT_DATE);

INSERT INTO river_job (id,state,max_attempts,args,kind,attempted_by,attempted_at) VALUES
  (101,'available',3,'{"keyword":"plumbers","state":"oh"}','scrape',NULL,NULL),
  (102,'running',3,'{"keyword":"electricians","state":"oh"}','scrape',ARRAY['worker-id-1_started'],NOW()),
  (103,'available',3,'{"keyword":"plumbers","state":"ky"}','maintenance',NULL,NULL);

INSERT INTO scrape_results (job_id,keyword,result_count,phone_count,created_at) VALUES
  (101,'plumbers',0,0,NOW()-interval '2 seconds'),
  (102,'',8,3,NOW()-interval '1 second'),
  (103,'plumbers',4,0,NOW()-interval '3 seconds');

INSERT INTO worker_heartbeats (box_id,container_name,container_id,reported_at,active_jobs,jobs_processed,results_per_min,status) VALUES
  ('box1','worker-1','worker-id-1',NOW(),1,42,18.5,'ok');

INSERT INTO stack_samples (
  box_id,captured_at,host_uptime_seconds,cpu_percent,load_1,memory_used_bytes,memory_total_bytes,
  disk_used_bytes,disk_total_bytes,spool_pending_files,spool_oldest_seconds,expected_workers,
  running_workers,unhealthy_workers,worker_restarts,database_ok,dashboard_ok,queue_api_ok,
  required_services_ok,services,queue_depth,running_jobs,retryable_jobs,oldest_queue_seconds,
  businesses_total,completed_jobs_total,empty_rate_1h
) VALUES
  ('box1',date_trunc('minute',NOW()-interval '1 hour'),7200,10,0.5,2147483648,8589934592,
   10737418240,107374182400,0,0,1,1,0,0,true,true,true,true,
   '{"docker.service":{"load":"loaded","active":"active","sub":"running"},"gms-serve.service":{"load":"loaded","active":"active","sub":"running"},"gms-enqueue.service":{"load":"loaded","active":"active","sub":"running"},"gms-heartbeat.timer":{"load":"loaded","active":"active","sub":"waiting"},"gms-ship.path":{"load":"loaded","active":"active","sub":"waiting"},"gms-ship.timer":{"load":"loaded","active":"active","sub":"waiting"},"gms-alert.timer":{"load":"loaded","active":"active","sub":"waiting"},"gms-export.timer":{"load":"loaded","active":"active","sub":"waiting"},"enqueue-trigger.path":{"load":"loaded","active":"active","sub":"waiting"},"gms-uptime.timer":{"load":"loaded","active":"active","sub":"waiting"},"external_heartbeat":{"load":"not-configured","active":"disabled","sub":"not configured"}}',
   1,1,0,30,3,1,0.1),
  ('box1',date_trunc('minute',NOW()),7300,12,0.7,3221225472,8589934592,
   11811160064,107374182400,0,0,1,1,0,0,true,true,true,true,
   '{"docker.service":{"load":"loaded","active":"active","sub":"running"},"gms-serve.service":{"load":"loaded","active":"active","sub":"running"},"gms-enqueue.service":{"load":"loaded","active":"active","sub":"running"},"gms-heartbeat.timer":{"load":"loaded","active":"active","sub":"waiting"},"gms-ship.path":{"load":"loaded","active":"active","sub":"waiting"},"gms-ship.timer":{"load":"loaded","active":"active","sub":"waiting"},"gms-alert.timer":{"load":"loaded","active":"active","sub":"waiting"},"gms-export.timer":{"load":"loaded","active":"active","sub":"waiting"},"enqueue-trigger.path":{"load":"loaded","active":"active","sub":"waiting"},"gms-uptime.timer":{"load":"loaded","active":"active","sub":"waiting"},"external_heartbeat":{"load":"not-configured","active":"disabled","sub":"not configured"}}',
   1,1,0,40,5,2,0.2);

INSERT INTO pipeline_alert_events (status,messages) VALUES ('ok','[]');
