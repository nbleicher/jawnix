-- +migrate Up
CREATE TABLE IF NOT EXISTS stack_samples (
    box_id                 TEXT NOT NULL,
    captured_at            TIMESTAMPTZ NOT NULL DEFAULT date_trunc('minute', NOW()),
    host_uptime_seconds    BIGINT NOT NULL DEFAULT 0,
    cpu_percent            DOUBLE PRECISION NOT NULL DEFAULT 0,
    load_1                 DOUBLE PRECISION NOT NULL DEFAULT 0,
    memory_used_bytes      BIGINT NOT NULL DEFAULT 0,
    memory_total_bytes     BIGINT NOT NULL DEFAULT 0,
    disk_used_bytes        BIGINT NOT NULL DEFAULT 0,
    disk_total_bytes       BIGINT NOT NULL DEFAULT 0,
    spool_pending_files    INTEGER NOT NULL DEFAULT 0,
    spool_oldest_seconds   BIGINT NOT NULL DEFAULT 0,
    expected_workers       INTEGER NOT NULL DEFAULT 0,
    running_workers        INTEGER NOT NULL DEFAULT 0,
    unhealthy_workers      INTEGER NOT NULL DEFAULT 0,
    worker_restarts        BIGINT NOT NULL DEFAULT 0,
    database_ok            BOOLEAN NOT NULL DEFAULT FALSE,
    dashboard_ok           BOOLEAN NOT NULL DEFAULT FALSE,
    queue_api_ok           BOOLEAN NOT NULL DEFAULT FALSE,
    required_services_ok   BOOLEAN NOT NULL DEFAULT FALSE,
    services               JSONB NOT NULL DEFAULT '{}'::jsonb,
    queue_depth            INTEGER NOT NULL DEFAULT 0,
    running_jobs           INTEGER NOT NULL DEFAULT 0,
    retryable_jobs         INTEGER NOT NULL DEFAULT 0,
    oldest_queue_seconds   BIGINT NOT NULL DEFAULT 0,
    businesses_total       BIGINT NOT NULL DEFAULT 0,
    completed_jobs_total   BIGINT NOT NULL DEFAULT 0,
    empty_rate_1h          DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (box_id, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_stack_samples_captured_at
    ON stack_samples (captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_businesses_first_seen
    ON businesses (first_seen);

CREATE TABLE IF NOT EXISTS pipeline_alert_events (
    id          BIGSERIAL PRIMARY KEY,
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      TEXT NOT NULL,
    messages    JSONB NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT pipeline_alert_events_status_check
        CHECK (status IN ('ok', 'alert', 'error'))
);

CREATE INDEX IF NOT EXISTS idx_pipeline_alert_events_checked_at
    ON pipeline_alert_events (checked_at DESC);

-- +migrate Down
DROP TABLE IF EXISTS pipeline_alert_events;
DROP TABLE IF EXISTS stack_samples;
DROP INDEX IF EXISTS idx_businesses_first_seen;
