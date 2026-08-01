-- +migrate Up
ALTER TABLE keyword_generations
  DROP CONSTRAINT IF EXISTS keyword_generations_mode_check;
ALTER TABLE keyword_generations
  ADD CONSTRAINT keyword_generations_mode_check
  CHECK (mode IN ('broad', 'adjacent', 'auto'));

CREATE TABLE IF NOT EXISTS keyword_rollover_events (
    id                BIGSERIAL PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status            TEXT NOT NULL CHECK (status IN ('generated', 'error')),
    generation_id     UUID REFERENCES keyword_generations(id) ON DELETE SET NULL,
    previous_keywords JSONB,
    next_keywords     JSONB,
    message           TEXT
);

CREATE INDEX IF NOT EXISTS idx_keyword_rollover_events_created_at
  ON keyword_rollover_events (created_at DESC);

INSERT INTO app_config (key, value, encrypted)
VALUES ('auto_keyword_rollover', 'false', false)
ON CONFLICT (key) DO NOTHING;

-- +migrate Down
DELETE FROM app_config WHERE key='auto_keyword_rollover';
DROP TABLE IF EXISTS keyword_rollover_events;
UPDATE keyword_generations SET mode='broad' WHERE mode='auto';
ALTER TABLE keyword_generations
  DROP CONSTRAINT IF EXISTS keyword_generations_mode_check;
ALTER TABLE keyword_generations
  ADD CONSTRAINT keyword_generations_mode_check
  CHECK (mode IN ('broad', 'adjacent'));
