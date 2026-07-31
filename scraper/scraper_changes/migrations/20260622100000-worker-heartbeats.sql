-- +migrate Up
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    box_id           TEXT NOT NULL,
    container_name   TEXT NOT NULL,
    reported_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active_jobs      INT NOT NULL DEFAULT 0,
    jobs_processed   BIGINT NOT NULL DEFAULT 0,
    results_per_min  FLOAT NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'unknown',
    PRIMARY KEY (box_id, container_name)
);

-- +migrate Down
DROP TABLE IF EXISTS worker_heartbeats;
