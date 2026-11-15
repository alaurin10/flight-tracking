-- 003_notifications: every alert is a record first and a delivery second (M4).
-- The worker writes one row per price alert, deal alert or health message before
-- handing it to any channel, so a failed channel never loses it; the app shows them.
-- Only adds a table.

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                    -- price | deal | health
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS notifications_created ON notifications(created_at);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
