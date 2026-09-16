"""The append-only invariant, enforced by the database itself."""

import sqlite3

import pytest

from helpers import add_obs, first_query_id, make_db


def test_observations_reject_update(tmp_path):
    conn, _ = make_db(tmp_path)
    qid = first_query_id(conn)
    add_obs(conn, qid, 20000)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE observations SET price_cents = 1")


def test_observations_reject_delete(tmp_path):
    conn, _ = make_db(tmp_path)
    qid = first_query_id(conn)
    add_obs(conn, qid, 20000)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM observations")


def test_schema_is_reapplied_safely(tmp_path):
    from flighttrack import db

    conn, _ = make_db(tmp_path)
    qid = first_query_id(conn)
    add_obs(conn, qid, 20000)
    conn.close()

    conn2 = db.connect(tmp_path / "t.db")  # reopening must not wipe anything
    assert conn2.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
