-- +migrate Up
ALTER TABLE worker_heartbeats
    ADD COLUMN IF NOT EXISTS container_id TEXT;

CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_container_id
    ON worker_heartbeats (container_id);

-- +migrate Down
DROP INDEX IF EXISTS idx_worker_heartbeats_container_id;
ALTER TABLE worker_heartbeats DROP COLUMN IF EXISTS container_id;
