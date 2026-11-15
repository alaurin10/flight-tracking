"""Numbered migrations, and adopting a database made by the M1 code."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flighttrack.store import db

# The M1 schema.sql, kept verbatim. The M1 code applied it on every connect and
# then added LEGACY_COLUMNS with ALTER TABLE.
M1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_m1.sql").read_text()

TABLES = (
    "routes",
    "queries",
    "observations",
    "alerts_sent",
    "run_log",
    "fetch_attempts",
    "state",
    "deal_posts",
    "feed_log",
)


def _m1_database(path: Path, *, with_legacy_columns: bool = True) -> None:
    """Build a database the way the M1 code did, and put a row in every table."""
    conn = sqlite3.connect(path)
    conn.executescript(M1_SCHEMA)
    if with_legacy_columns:
        for table, column, decl in db.LEGACY_COLUMNS:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.executescript(
        """
        INSERT INTO routes (id, origin, destination, label, target_price, priority)
            VALUES (1, 'SEA', 'SLC', 'Salt Lake City', 14000, 1);
        INSERT INTO queries (id, route_id, depart_date, return_date, pattern, last_fetch_at)
            VALUES (1, 1, '2027-01-15', '2027-01-18', 'ski_weekend', '2026-11-14T10:00:00Z');
        INSERT INTO observations (id, query_id, observed_at, price_cents, airline, is_best, raw)
            VALUES (1, 1, '2026-11-14T10:00:00Z', 39400, 'Alaska', 1, '{}'),
                   (2, 1, '2026-11-14T10:00:00Z', 41200, 'Delta', 0, '{}');
        INSERT INTO alerts_sent (query_id, observation_id, sent_at, reason, price_cents)
            VALUES (1, 1, '2026-11-14T10:01:00Z', 'threshold', 39400);
        INSERT INTO run_log (id, started_at, finished_at, attempted, succeeded)
            VALUES (1, '2026-11-14T10:00:00Z', '2026-11-14T10:02:00Z', 1, 1);
        INSERT INTO fetch_attempts (run_id, query_id, at, source, outcome, latency_ms)
            VALUES (1, 1, '2026-11-14T10:00:05Z', 'google_html', 'ok', 900);
        INSERT INTO state (key, value) VALUES ('last_success_at', '2026-11-14T10:02:00Z');
        INSERT INTO deal_posts (guid, feed, title, seen_at, price_cents, watch)
            VALUES ('g1', 'tfd', 'Seattle to Tokyo $598', '2026-11-14T09:00:00Z', 59800, 'Japan');
        INSERT INTO feed_log (feed, at, ok, items) VALUES ('tfd', '2026-11-14T09:00:00Z', 1, 16);
        """
    )
    conn.commit()
    conn.close()


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    """Every row of every M1 table, by column name, so column order does not matter."""
    out: dict[str, list[tuple[object, ...]]] = {}
    for table in TABLES:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        old = [c for c in cols if (table, c) not in {(t, c) for t, c, _ in db.LEGACY_COLUMNS}]
        rows = conn.execute(f"SELECT {', '.join(old)} FROM {table} ORDER BY rowid").fetchall()
        out[table] = [tuple(r) for r in rows]
    return out


@pytest.mark.parametrize("with_legacy_columns", [True, False])
def test_m1_database_is_adopted_intact(tmp_path: Path, with_legacy_columns: bool) -> None:
    path = tmp_path / "flights.db"
    _m1_database(path, with_legacy_columns=with_legacy_columns)
    before = _snapshot(sqlite3.connect(path))

    conn = db.connect(path)
    # 001 is recorded without running; the later ones run as usual.
    files = sorted(p.stem for p in db.MIGRATIONS_DIR.glob("*.sql"))
    assert db.migrate(conn) == files and files[:2] == ["001_init", "002_watches"]

    recorded = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert recorded == files
    assert _snapshot(conn) == before  # every row intact
    for table, column, _ in db.LEGACY_COLUMNS:
        assert column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    assert {"observations_no_update", "observations_no_delete"} <= db.trigger_names(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE observations SET price_cents = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM observations")

    # A second run changes nothing.
    assert db.migrate(conn) == []
    assert _snapshot(conn) == before


def test_new_database_gets_every_migration(tmp_path: Path) -> None:
    conn = db.open_db(tmp_path / "new.db")
    files = sorted(p.stem for p in db.MIGRATIONS_DIR.glob("*.sql"))
    recorded = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert recorded == files and files[0] == "001_init"
    assert set(TABLES) <= db.table_names(conn)
    assert {"observations_no_update", "observations_no_delete"} <= db.trigger_names(conn)
    assert db.migrate(conn) == []


def test_new_database_matches_an_adopted_one(tmp_path: Path) -> None:
    """001_init and the M1 schema plus its ALTERs give the same columns."""
    fresh = db.open_db(tmp_path / "fresh.db")
    _m1_database(tmp_path / "old.db")
    old = db.open_db(tmp_path / "old.db")
    for table in TABLES:
        cols_fresh = [r[1] for r in fresh.execute(f"PRAGMA table_info({table})")]
        cols_old = [r[1] for r in old.execute(f"PRAGMA table_info({table})")]
        assert cols_fresh == cols_old, table


def test_connect_settings(tmp_path: Path) -> None:
    conn = db.open_db(tmp_path / "f.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_readonly_connection_never_creates_or_writes(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        db.connect(missing, readonly=True)
    assert not missing.exists()

    db.open_db(tmp_path / "f.db").close()
    ro = db.connect(tmp_path / "f.db", readonly=True)
    assert ro.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro.execute("INSERT INTO state (key, value) VALUES ('k', 'v')")


def test_missing_trigger_is_put_back(tmp_path: Path) -> None:
    conn = db.open_db(tmp_path / "f.db")
    conn.execute("DROP TRIGGER observations_no_delete")
    conn.commit()
    db.migrate(conn)
    assert "observations_no_delete" in db.trigger_names(conn)


def test_failed_migration_is_rolled_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for p in db.MIGRATIONS_DIR.glob("*.sql"):
        (migrations / p.name).write_text(p.read_text())
    (migrations / "999_broken.sql").write_text(
        "CREATE TABLE half_done (id INTEGER);\nTHIS IS NOT SQL;\n"
    )
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migrations)

    conn = db.connect(tmp_path / "f.db")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(conn)
    assert "half_done" not in db.table_names(conn)
    recorded = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert "999_broken" not in recorded and "001_init" in recorded
