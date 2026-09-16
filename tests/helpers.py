"""Shared fixtures. Everything here is offline — no test touches the network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flighttrack import db, expand
from flighttrack.config import Alerts, Config, Fetch, Pattern, Route, Search


def make_config(**over) -> Config:
    base = dict(
        home="SEA",
        routes=[
            Route("HND", "Tokyo", 65000, 1, None),
            Route("NRT", "Tokyo", 60000, 2, None),
            Route("SLC", "Salt Lake City", 14000, 1, None),
        ],
        patterns=[
            Pattern("extended_weekend_thu", "THU", 4),
            Pattern("full_week", "SAT", 7),
        ],
        min_days_ahead=21,
        max_days_ahead=60,
        search=Search(),
        fetch=Fetch(max_queries_per_run=5, min_sleep_seconds=0, max_sleep_seconds=0),
        alerts=Alerts(),
        notify={"channel": "none"},
        db_path=":memory:",
    )
    base.update(over)
    return Config(**base)


def make_db(tmp_path, cfg=None, build_links=False):
    cfg = cfg or make_config()
    conn = db.connect(tmp_path / "t.db")
    expand.expand(conn, cfg, build_links=build_links)
    return conn, cfg


def add_obs(conn, query_id, price_cents, days_ago=0, is_best=1, airline="Alaska", stops=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        """INSERT INTO observations
           (query_id, observed_at, price_cents, currency, airline, stops, duration_min, is_best, raw)
           VALUES (?, ?, ?, 'USD', ?, ?, 600, ?, '{}')""",
        (query_id, ts, price_cents, airline, stops, is_best),
    )
    conn.commit()
    return cur.lastrowid


def first_query_id(conn, dest="SLC"):
    return conn.execute(
        "SELECT q.id FROM queries q JOIN routes r ON r.id=q.route_id "
        "WHERE r.destination=? ORDER BY q.depart_date LIMIT 1",
        (dest,),
    ).fetchone()[0]
