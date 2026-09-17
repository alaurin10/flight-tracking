"""Expansion: idempotent, horizon-respecting, never destructive."""

from datetime import date, timedelta

from flighttrack import expand
from helpers import make_config, make_db


def test_expands_full_grid(tmp_path):
    cfg = make_config()
    conn, cfg = make_db(tmp_path, cfg)
    n = conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
    # 3 routes x 2 patterns x ~6 matching weekdays in a 21-60 day horizon
    assert n > 0
    assert n == conn.execute("SELECT COUNT(*) FROM queries WHERE active=1").fetchone()[0]


def test_is_idempotent(tmp_path):
    cfg = make_config()
    conn, cfg = make_db(tmp_path, cfg)
    before = conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
    result = expand.expand(conn, cfg, build_links=False)
    assert result.queries_inserted == 0
    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == before


def test_respects_horizon(tmp_path):
    cfg = make_config(min_days_ahead=30, max_days_ahead=45)
    conn, cfg = make_db(tmp_path, cfg)
    lo = (date.today() + timedelta(days=30)).isoformat()
    hi = (date.today() + timedelta(days=45)).isoformat()
    for row in conn.execute("SELECT depart_date FROM queries"):
        assert lo <= row[0] <= hi


def test_departure_dates_land_on_requested_weekday(tmp_path):
    cfg = make_config()
    for pattern in cfg.patterns:
        for d in expand.departure_dates(cfg, pattern.dow_index):
            assert d.weekday() == pattern.dow_index


def test_return_date_follows_nights(tmp_path):
    cfg = make_config()
    conn, cfg = make_db(tmp_path, cfg)
    nights = {p.name: p.nights for p in cfg.patterns}
    for row in conn.execute("SELECT depart_date, return_date, pattern FROM queries"):
        dep = date.fromisoformat(row[0])
        ret = date.fromisoformat(row[1])
        assert (ret - dep).days == nights[row[2]]


def test_expired_queries_are_deactivated_not_deleted(tmp_path):
    cfg = make_config()
    conn, cfg = make_db(tmp_path, cfg)
    total = conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]

    # Re-expand as if a year has passed: every existing departure is now past.
    future = date.today() + timedelta(days=400)
    expand.expand(conn, cfg, today=future, build_links=False)

    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0] > total  # nothing lost
    stale = conn.execute(
        "SELECT COUNT(*) FROM queries WHERE depart_date < ? AND active = 1", (future.isoformat(),)
    ).fetchone()[0]
    assert stale == 0


def test_dropped_route_is_deactivated_not_deleted(tmp_path):
    cfg = make_config()
    conn, cfg = make_db(tmp_path, cfg)

    trimmed = make_config(routes=[r for r in cfg.routes if r.dest != "NRT"])
    expand.expand(conn, trimmed, build_links=False)

    row = conn.execute("SELECT active FROM routes WHERE destination='NRT'").fetchone()
    assert row is not None and row[0] == 0
