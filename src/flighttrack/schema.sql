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

-- ---------------------------------------------------------------------------
-- Added in v2 (robustness work). All CREATE IF NOT EXISTS; column additions
-- to older tables are applied by db.migrate().
-- ---------------------------------------------------------------------------

-- Every request we make, whatever happened to it. `run_log` is the summary;
-- this is the evidence. It is what `calibrate` measured once, measured
-- continuously.
CREATE TABLE IF NOT EXISTS fetch_attempts (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER REFERENCES run_log(id),
    query_id    INTEGER REFERENCES queries(id),
    at          TEXT    NOT NULL,
    source      TEXT,                              -- which source answered / failed
    outcome     TEXT    NOT NULL,                  -- ok | empty | blocked | network | layout | unknown
    latency_ms  INTEGER,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_time ON fetch_attempts(at);

-- Small key/value store for operational state that must survive between
-- cron invocations: cooldown_until, blocked_streak, last_health_alert_at.
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Deal-feed posts that matched a watch (and, for dedup, those we alerted on).
CREATE TABLE IF NOT EXISTS deal_posts (
    id           INTEGER PRIMARY KEY,
    guid         TEXT    NOT NULL UNIQUE,
    feed         TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    link         TEXT,
    published_at TEXT,
    seen_at      TEXT    NOT NULL,
    price_cents  INTEGER,
    watch        TEXT,                             -- name of the watch it matched, NULL if none
    alerted_at   TEXT
);

-- One row per feed poll, so a dead feed is visible in `health`.
CREATE TABLE IF NOT EXISTS feed_log (
    id        INTEGER PRIMARY KEY,
    feed      TEXT NOT NULL,
    at        TEXT NOT NULL,
    ok        INTEGER NOT NULL,
    items     INTEGER NOT NULL DEFAULT 0,
    error     TEXT
);
