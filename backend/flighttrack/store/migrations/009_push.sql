-- 009_push: the owner's devices for browser push alerts (M8). One row per device; each
-- chooses which kinds of alert it gets (price, deal, health; all on by default). Only
-- adds a table; nothing that holds price history is touched.

CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint    TEXT PRIMARY KEY,               -- the browser push service's address
    keys_json   TEXT NOT NULL,                  -- {"p256dh": …, "auth": …}
    kinds       TEXT NOT NULL DEFAULT '["price","deal","health"]',  -- JSON
    created_at  TEXT NOT NULL,
    last_ok_at  TEXT,
    fail_count  INTEGER NOT NULL DEFAULT 0
);
