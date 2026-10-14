-- +migrate Up
CREATE TABLE IF NOT EXISTS acquisition_categories (
    id text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
    canonical_query text NOT NULL UNIQUE,
    started_on date NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    origin text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS acquisition_category_aliases (
    alias text PRIMARY KEY,
    category_id text NOT NULL REFERENCES acquisition_categories(id) ON DELETE CASCADE,
    origin text NOT NULL
);
CREATE INDEX IF NOT EXISTS acquisition_category_aliases_category ON acquisition_category_aliases(category_id);
CREATE TABLE IF NOT EXISTS acquisition_category_cells (
    category_id text NOT NULL REFERENCES acquisition_categories(id) ON DELETE CASCADE,
    state text NOT NULL CHECK (state ~ '^[a-z]{2}$'),
    cell text NOT NULL,
    enqueued_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(category_id,state,cell)
);
CREATE TABLE IF NOT EXISTS acquisition_category_states (
    category_id text NOT NULL REFERENCES acquisition_categories(id) ON DELETE CASCADE,
    state text NOT NULL CHECK (state ~ '^[a-z]{2}$'),
    started_on date NOT NULL,
    cells jsonb NOT NULL CHECK (jsonb_typeof(cells)='array'),
    PRIMARY KEY(category_id,state)
);
CREATE INDEX IF NOT EXISTS keyword_yield_evaluations_normalized ON keyword_yield_evaluations(lower(keyword),state);
CREATE INDEX IF NOT EXISTS keyword_history_normalized ON keyword_history(lower(keyword),state);
CREATE INDEX IF NOT EXISTS enqueue_log_category_coverage ON enqueue_log(lower(keyword),state,cell) WHERE status='posted';
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='gmaps_ro') THEN
        GRANT SELECT ON acquisition_categories,acquisition_category_aliases,acquisition_category_cells,acquisition_category_states TO gmaps_ro;
    END IF;
END $$;

-- +migrate Down
DROP INDEX IF EXISTS enqueue_log_category_coverage;
DROP INDEX IF EXISTS keyword_history_normalized;
DROP INDEX IF EXISTS keyword_yield_evaluations_normalized;
DROP TABLE IF EXISTS acquisition_category_states;
DROP TABLE IF EXISTS acquisition_category_cells;
DROP TABLE IF EXISTS acquisition_category_aliases;
DROP TABLE IF EXISTS acquisition_categories;
