-- flighttrack schema. See build plan §5.
--
-- The central invariant: `observations` is append-only. Every historical
-- percentile, every all-time low, every "is this price good" judgement the
-- system can make depends on nothing ever rewriting that table. The triggers
-- at the bottom enforce it at the database level rather than by convention.

CREATE TABLE IF NOT EXISTS routes (
    id            INTEGER PRIMARY KEY,
    origin        TEXT    NOT NULL,              -- 'SEA'
    destination   TEXT    NOT NULL,              -- 'HND'
    label         TEXT    NOT NULL,              -- 'Tokyo' (shared by HND + NRT)
    target_price  INTEGER,                       -- cents; nullable, see alerting
    priority      INTEGER NOT NULL DEFAULT 2,    -- 1=daily, 2=every 3d, 3=weekly
    max_stops     INTEGER,                       -- NULL = record everything
    active        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(origin, destination)
);

CREATE TABLE IF NOT EXISTS queries (
    id            INTEGER PRIMARY KEY,
    route_id      INTEGER NOT NULL REFERENCES routes(id),
    depart_date   TEXT    NOT NULL,              -- ISO 'YYYY-MM-DD'
    return_date   TEXT,                          -- NULL for one-way
    pattern       TEXT    NOT NULL,              -- 'extended_weekend_thu' etc.
    active        INTEGER NOT NULL DEFAULT 1,
    last_fetch_at TEXT,
    deep_link     TEXT,                          -- Google Flights URL (§12)
    UNIQUE(route_id, depart_date, return_date)
);

CREATE INDEX IF NOT EXISTS idx_queries_due
    ON queries(active, last_fetch_at);

CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY,
    query_id      INTEGER NOT NULL REFERENCES queries(id),
    observed_at   TEXT    NOT NULL,              -- ISO8601 UTC
    price_cents   INTEGER NOT NULL,
    currency      TEXT    NOT NULL DEFAULT 'USD',
    airline       TEXT,
    stops         INTEGER,
    duration_min  INTEGER,
    is_best       INTEGER NOT NULL DEFAULT 0,    -- cheapest of this fetch
    raw           TEXT                           -- JSON blob, for post-hoc debugging
);

CREATE INDEX IF NOT EXISTS idx_obs_query_time
    ON observations(query_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_best
    ON observations(query_id, is_best, observed_at);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id             INTEGER PRIMARY KEY,
    query_id       INTEGER NOT NULL REFERENCES queries(id),
    observation_id INTEGER NOT NULL REFERENCES observations(id),
    sent_at        TEXT    NOT NULL,
    reason         TEXT    NOT NULL,             -- 'threshold' | 'percentile' | 'all_time_low'
    price_cents    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_query_time
    ON alerts_sent(query_id, sent_at);

CREATE TABLE IF NOT EXISTS run_log (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    attempted     INTEGER NOT NULL DEFAULT 0,
    succeeded     INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    blocked       INTEGER NOT NULL DEFAULT 0,    -- 1 if we aborted on rate limiting
    notes         TEXT
);

-- Append-only enforcement. The plan says "never UPDATE, never DELETE"; this
-- makes that a property of the database rather than a rule someone remembers.
-- Correcting bad data means recording a new observation, not editing an old one.
CREATE TRIGGER IF NOT EXISTS observations_no_update
BEFORE UPDATE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS observations_no_delete
BEFORE DELETE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations is append-only: DELETE is forbidden');
END;
