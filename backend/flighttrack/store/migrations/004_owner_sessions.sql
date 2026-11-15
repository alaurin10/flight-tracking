-- 004_owner_sessions: owner sign-in (M5), as in Snow Watch. One row per signed-in
-- browser. The cookie holds a random token; only its SHA-256 is stored, so a copy
-- of the database cannot be used to sign in. Only adds a table.

CREATE TABLE IF NOT EXISTS owner_sessions (
    token_hash  TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
