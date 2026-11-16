-- 006_scans: one row per repeating Google call that is not a single date pair, so the
-- worker can run each when it is stale instead of once a day (deal finder track, D1).
-- Today: a calendar window of a flexible search (kind 'calendar'); from D2 also Explore
-- boards (kind 'explore'). `key` is stable across days; the dates are worked out again
-- each time it runs. Only adds a table; nothing that holds price history is touched.

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                    -- calendar | explore
    key TEXT NOT NULL UNIQUE,              -- e.g. 'SEA|SLC|ski-weekends|0'
    spec TEXT NOT NULL DEFAULT '{}',       -- JSON: what it asks Google, for the status page
    active INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,                      -- last time it ran to an answer (ok or unreadable)
    last_ok_at TEXT,
    last_outcome TEXT,                     -- ok | layout | gated | blocked | network
    fail_streak INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS scans_due ON scans(kind, active, last_run_at);
