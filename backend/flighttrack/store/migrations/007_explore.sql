-- 007_explore: whole regions priced with Google's Explore call (deal finder track, D2).
-- A region watch gains the trip length and the airport its Explore boards are priced for,
-- and the fares Explore returns are kept, append-only, for the boards and for D3's usual
-- prices. Only adds columns, a table, indexes and triggers; nothing that holds price
-- history is touched.

ALTER TABLE deal_watches ADD COLUMN nights INTEGER;  -- trip length priced; NULL = a week; 0 = one way
ALTER TABLE deal_watches ADD COLUMN origin TEXT;     -- IATA priced from; NULL = HOME_AIRPORT

CREATE TABLE IF NOT EXISTS explore_prices (
    id           INTEGER PRIMARY KEY,
    origin       TEXT    NOT NULL,              -- 'SEA'
    region       TEXT    NOT NULL,              -- the Explore place asked: 'anywhere', 'asia', …
    place_id     TEXT    NOT NULL,              -- Google's knowledge-graph id of the destination
    name         TEXT    NOT NULL,              -- 'Tokyo'
    country      TEXT,
    airport      TEXT,                          -- where the cheapest itinerary lands
    depart_date  TEXT    NOT NULL,
    nights       INTEGER,                       -- NULL = one way
    price_cents  INTEGER NOT NULL,
    currency     TEXT    NOT NULL DEFAULT 'USD',
    airline      TEXT,
    stops        INTEGER,
    duration_min INTEGER,
    observed_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS explore_prices_board
    ON explore_prices(origin, region, nights, depart_date, observed_at);
CREATE INDEX IF NOT EXISTS explore_prices_place
    ON explore_prices(origin, place_id, nights, depart_date, observed_at);

-- Like observations: a fare once seen is never rewritten.
CREATE TRIGGER IF NOT EXISTS explore_prices_no_update
BEFORE UPDATE ON explore_prices
BEGIN
    SELECT RAISE(ABORT, 'explore_prices is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS explore_prices_no_delete
BEFORE DELETE ON explore_prices
BEGIN
    SELECT RAISE(ABORT, 'explore_prices is append-only: DELETE is forbidden');
END;
