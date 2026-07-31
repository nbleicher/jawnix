-- +migrate Up
-- Add explicit enqueue states so the producer can reserve before POSTing while
-- still retrying transient API failures.
ALTER TABLE enqueue_log ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'posted';
ALTER TABLE enqueue_log ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE enqueue_log ADD COLUMN IF NOT EXISTS last_error TEXT;

-- +migrate StatementBegin
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'enqueue_log_status_check'
    ) THEN
        ALTER TABLE enqueue_log ADD CONSTRAINT enqueue_log_status_check
            CHECK (status IN ('reserved', 'posted', 'failed'));
    END IF;
END $$;
-- +migrate StatementEnd

UPDATE enqueue_log SET status = 'posted' WHERE status IS NULL;
CREATE INDEX IF NOT EXISTS idx_enqueue_log_status ON enqueue_log (status);

-- +migrate Down
DROP INDEX IF EXISTS idx_enqueue_log_status;
ALTER TABLE enqueue_log DROP CONSTRAINT IF EXISTS enqueue_log_status_check;
ALTER TABLE enqueue_log DROP COLUMN IF EXISTS last_error;
ALTER TABLE enqueue_log DROP COLUMN IF EXISTS updated_at;
ALTER TABLE enqueue_log DROP COLUMN IF EXISTS status;
