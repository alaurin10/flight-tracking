-- 008_finds: deals the app found itself (deal finder track, D3). A find starts as a
-- candidate from an Explore board (a fare well under the place's usual price, under a
-- watch's price, or a new low), is confirmed or turned down by one real results-page
-- search with the owner's filters, and is checked again until it is gone. Only adds a
-- table; nothing that holds price history is touched.

CREATE TABLE IF NOT EXISTS finds (
    id             INTEGER PRIMARY KEY,
    key            TEXT    NOT NULL UNIQUE,     -- origin|airport|depart|nights
    origin         TEXT    NOT NULL,
    airport        TEXT    NOT NULL,
    place_id       TEXT,
    name           TEXT    NOT NULL,
    country        TEXT,
    depart_date    TEXT    NOT NULL,
    nights         INTEGER,                     -- NULL = one way
    board_key      TEXT,                        -- the board it was found on
    estimate_cents INTEGER NOT NULL,            -- the Explore fare that raised it
    usual_cents    INTEGER,                     -- the place's usual fare then; NULL = too little history
    why            TEXT    NOT NULL,            -- in words: '32% under its usual $640'
    status         TEXT    NOT NULL DEFAULT 'candidate',  -- candidate | confirmed | gone
    price_cents    INTEGER,                     -- the real price, from the results page
    airline        TEXT,
    stops          INTEGER,
    duration_min   INTEGER,
    note           TEXT,                        -- why it is gone, or the last check's trouble
    found_at       TEXT    NOT NULL,
    checked_at     TEXT,
    confirmed_at   TEXT,
    notified_cents INTEGER,                     -- the price last alerted at
    checks         INTEGER NOT NULL DEFAULT 0,  -- failed checks in a row
    max_cents      INTEGER                      -- the watch's price, when that raised it
);

CREATE INDEX IF NOT EXISTS finds_status ON finds(status, checked_at);
