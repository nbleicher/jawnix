-- +migrate Up
-- Control-plane ledger for idempotent enqueue (O-11) and campaign dedup (O-12).
-- Written/read by control/enqueue.py on the control VPS.

-- O-11: one row per (keyword, state, cell, day) actually enqueued. The UNIQUE
-- constraint makes re-running / restarting the enqueuer idempotent — a cell is
-- never queued twice in the same day.
CREATE TABLE IF NOT EXISTS enqueue_log (
    keyword     TEXT NOT NULL,
    state       TEXT NOT NULL,
    cell        TEXT NOT NULL,
    day         DATE NOT NULL,
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    UNIQUE (keyword, state, cell, day)
);
CREATE INDEX IF NOT EXISTS idx_enqueue_log_kw_state_day ON enqueue_log (keyword, state, day);

-- O-12: campaign history. Move from the local SQLite (util/keyword_db.py) so a
-- distributed control plane shares it. last_enqueued lets the enqueuer skip a
-- (keyword, state) pair that was fully run within a look-back window.
CREATE TABLE IF NOT EXISTS keyword_history (
    keyword       TEXT NOT NULL,
    state         TEXT NOT NULL,
    last_enqueued DATE NOT NULL,
    UNIQUE (keyword, state)
);
CREATE INDEX IF NOT EXISTS idx_keyword_history_state ON keyword_history (state);

-- +migrate Down
DROP TABLE IF EXISTS keyword_history;
DROP TABLE IF EXISTS enqueue_log;
