"""SQLite connection setup, migrations, and the helpers every module uses.

`migrate()` applies `migrations/NNN_*.sql` in order and records each one in
`schema_migrations`. Migrations only add: price history cannot be collected
again, so nothing here drops or rewrites a table that holds it.

A database made before numbered migrations (the M1 code applied `schema.sql`
on every connect and added three columns with ALTER TABLE) is adopted: the
missing columns are added the old way, then `001_init` is recorded as applied
without being run.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_VERSION = re.compile(r"^\d{3}_[a-z0-9_]+$")

# Columns the pre-migration code added with ALTER TABLE: (table, column, declaration).
LEGACY_COLUMNS = (
    ("observations", "source", "TEXT"),
    ("run_log", "kind", "TEXT NOT NULL DEFAULT 'fetch'"),
    ("run_log", "skipped", "INTEGER NOT NULL DEFAULT 0"),
)

# `observations` is append-only. Correcting bad data means recording a new
# observation, never editing an old one. `compact` is the one sanctioned
# exception: it drops the delete trigger, removes old runner-up offers and puts
# the trigger back, all in one transaction.
OBSERVATIONS_NO_UPDATE = """CREATE TRIGGER IF NOT EXISTS observations_no_update
BEFORE UPDATE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations is append-only: UPDATE is forbidden');
END"""
OBSERVATIONS_NO_DELETE = """CREATE TRIGGER IF NOT EXISTS observations_no_delete
BEFORE DELETE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations is append-only: DELETE is forbidden');
END"""
APPEND_ONLY_TRIGGERS = (OBSERVATIONS_NO_UPDATE, OBSERVATIONS_NO_DELETE)


def utcnow() -> str:
    """ISO 8601 UTC timestamp, second resolution, always suffixed 'Z'."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the database in WAL mode. Read-only connections never create the file.

    `busy_timeout` lets two processes (a long-running server and a command typed
    in a shell) share the file: a writer waits up to five seconds for the other
    instead of failing at once. Connections may cross threads; each still serves
    one job at a time.
    """
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    else:
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def open_db(path: str | Path) -> sqlite3.Connection:
    """Connect for writing and bring the schema up to date: what commands use."""
    conn = connect(path)
    migrate(conn)
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in rows}


def trigger_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def _record(conn: sqlite3.Connection, version: str) -> None:
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, utcnow())
    )


def _adopt(conn: sqlite3.Connection) -> list[str]:
    """Bring a pre-migration database to the 001_init shape without running 001."""
    added: list[str] = []
    with conn:
        for table, column, decl in LEGACY_COLUMNS:
            if table in table_names(conn) and column not in _columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                added.append(f"{table}.{column}")
        _record(conn, "001_init")
    return added


def ensure_append_only(conn: sqlite3.Connection) -> None:
    """Re-create the append-only triggers if anything ever removed them."""
    if "observations" not in table_names(conn):
        return
    with conn:
        for sql in APPEND_ONLY_TRIGGERS:
            conn.execute(sql)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations in order. Returns the versions recorded by this call."""
    done = applied_migrations(conn)
    applied: list[str] = []
    if not done and "observations" in table_names(conn):
        _adopt(conn)
        done.add("001_init")
        applied.append("001_init")
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in done:
            continue
        if not _VERSION.match(version):
            raise ValueError(f"bad migration file name: {path.name}")
        try:
            conn.executescript(
                f"BEGIN;\n{path.read_text()}\n"
                f"INSERT INTO schema_migrations (version, applied_at) "
                f"VALUES ('{version}', '{utcnow()}');\nCOMMIT;"
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
        applied.append(version)
    ensure_append_only(conn)
    return applied


# ---------------------------------------------------------------------------
# state: a small key/value table for operational state (cooldown_until, ...)
# ---------------------------------------------------------------------------


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    value: str | None = row["value"]
    return value


def set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM state WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    conn.commit()
