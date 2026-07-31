-- +migrate Up
-- Deduplicated record store (one row per unique business) + the cumulative
-- phone-keyed "global sheet" (leads). See PRD §12.

CREATE TABLE IF NOT EXISTS businesses (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dedup_key     TEXT NOT NULL UNIQUE,        -- place_id | cid | md5(norm name+phone+addr)
    place_id      TEXT,
    cid           TEXT,
    title         TEXT,
    phone         TEXT,
    website       TEXT,
    category      TEXT,
    address       TEXT,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    rating        DOUBLE PRECISION,
    review_count  INTEGER,
    emails        TEXT[],
    state         TEXT,
    keyword       TEXT,
    cell          TEXT,
    source_job_id BIGINT,
    raw           JSONB,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_businesses_state_keyword ON businesses (state, keyword);
CREATE INDEX IF NOT EXISTS idx_businesses_last_seen     ON businesses (last_seen);
CREATE INDEX IF NOT EXISTS idx_businesses_place_id      ON businesses (place_id);

CREATE TABLE IF NOT EXISTS leads (
    phone        TEXT PRIMARY KEY,             -- normalized 10-digit, the global dedup key
    title        TEXT,
    state        TEXT,
    first_seen   DATE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    business_id  BIGINT REFERENCES businesses(id)
);
CREATE INDEX IF NOT EXISTS idx_leads_first_seen ON leads (first_seen);
CREATE INDEX IF NOT EXISTS idx_leads_state      ON leads (state);

-- +migrate Down
DROP TABLE IF EXISTS leads;
DROP TABLE IF EXISTS businesses;
