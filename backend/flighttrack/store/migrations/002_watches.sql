-- 002_watches: what is tracked moves from config.yaml into the database (M3).
-- Trips (fixed dates), flexible searches, the links from both to the date pairs in
-- `queries`, deal watches, deal feeds, a key/value settings table and a job queue.
-- Only adds tables; nothing that holds price history is touched.

CREATE TABLE IF NOT EXISTS trips (                       -- fixed dates: "when do I book?"
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,             -- used in URLs and as queries.pattern
    name TEXT NOT NULL,
    origin TEXT NOT NULL, destination TEXT NOT NULL,      -- IATA
    depart_date TEXT NOT NULL, return_date TEXT,          -- NULL return = one way
    target_price INTEGER,                  -- cents
    max_stops INTEGER,
    status TEXT NOT NULL DEFAULT 'active', -- active | paused | booked | archived
    notes TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS searches (                    -- flexible dates: "which weekend is cheapest?"
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    origin TEXT NOT NULL,
    destinations TEXT NOT NULL,            -- JSON array of IATA codes
    depart_dows TEXT NOT NULL,             -- JSON array, e.g. ["THU","FRI"]
    nights INTEGER NOT NULL,               -- 0 = one way
    window_start TEXT, window_end TEXT,    -- both NULL = rolling horizon
    min_days_ahead INTEGER,
    target_price INTEGER, max_stops INTEGER,
    priority INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'active', -- active | paused | archived
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_queries (               -- which trip or search wants which date pair
    query_id INTEGER NOT NULL REFERENCES queries(id),
    trip_id INTEGER REFERENCES trips(id),
    search_id INTEGER REFERENCES searches(id),
    CHECK ((trip_id IS NULL) <> (search_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS watch_queries_trip
    ON watch_queries(query_id, trip_id) WHERE trip_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS watch_queries_search
    ON watch_queries(query_id, search_id) WHERE search_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS deal_watches (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    origins TEXT NOT NULL, destinations TEXT NOT NULL, keywords TEXT NOT NULL DEFAULT '[]',  -- JSON arrays
    max_price INTEGER, status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deal_feeds (
    id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, name TEXT,
    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);  -- JSON values

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | failed
    requested_by TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT
);
