"""SQLite access layer.

One file, no ORM, no migrations framework. The schema is small enough that
`schema.sql` applied idempotently is the whole story.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    """ISO8601 UTC timestamp, second resolution, always suffixed 'Z'.

    Every timestamp written to the database goes through here so that string
    comparison on the column is also chronological comparison.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database, applying the schema if it is not there yet."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL keeps an ad-hoc `sqlite3 flights.db` session from blocking a cron
    # fetch that happens to fire while you are poking at the data.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn
