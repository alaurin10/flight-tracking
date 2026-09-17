"""SQLite access layer.

One file, no ORM. `schema.sql` is applied idempotently on every connect, then
`migrate()` adds any columns that older databases lack. That is the whole
migration story: additive only, never destructive, safe to run every time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# (table, column, declaration) — added with ALTER TABLE when missing.
COLUMN_MIGRATIONS = [
    ("observations", "source", "TEXT"),
    ("run_log", "kind", "TEXT NOT NULL DEFAULT 'fetch'"),
    ("run_log", "skipped", "INTEGER NOT NULL DEFAULT 0"),
]


def utcnow() -> str:
    """ISO8601 UTC timestamp, second resolution, always suffixed 'Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring an existing database up to the current column set. Returns what changed."""
    applied: list[str] = []
    for table, column, decl in COLUMN_MIGRATIONS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column}")
    if applied:
        conn.commit()
    return applied


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database, applying the schema if it is not there yet."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL keeps an ad-hoc `sqlite3 flights.db` session from blocking a cron
    # fetch that happens to fire while you are poking at the data.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    migrate(conn)
    return conn


# ---------------------------------------------------------------------------
# state helpers
# ---------------------------------------------------------------------------

def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM state WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    conn.commit()
