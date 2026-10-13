-- +migrate Up
CREATE TABLE IF NOT EXISTS approved_campaign_keywords (
    keyword text PRIMARY KEY,
    started_on date NOT NULL,
    approved_version text NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS approved_campaign_keyword_normalized
    ON approved_campaign_keywords (lower(keyword));
CREATE TABLE IF NOT EXISTS state_campaign_backlog (
    keyword text NOT NULL REFERENCES approved_campaign_keywords(keyword),
    state text NOT NULL CHECK (state ~ '^[a-z]{2}$'),
    started_on date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (keyword, state)
);
CREATE INDEX IF NOT EXISTS state_campaign_backlog_state
    ON state_campaign_backlog(state, created_at);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='gmaps_ro') THEN
        GRANT SELECT ON approved_campaign_keywords, state_campaign_backlog TO gmaps_ro;
    END IF;
END $$;

-- +migrate Down
DROP TABLE IF EXISTS state_campaign_backlog;
DROP TABLE IF EXISTS approved_campaign_keywords;
