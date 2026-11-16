"""Seed a database with plausible synthetic history, so the app can be seen
before a single real price has been collected (and the e2e tests have data).

Deterministic (seeded), clearly synthetic (source='demo'), never written to
the real database path unless you point it there.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import UTC, date, datetime, timedelta

from . import expand as expand_mod
from . import watches
from .config import Config
from .store.db import set_state

BASE_PRICE = {"HND": 92000, "NRT": 84000, "SLC": 21000}
AIRLINES = {
    "HND": ["Alaska", "ANA", "Japan Airlines", "Delta"],
    "NRT": ["ZIPAIR", "Japan Airlines", "United"],
    "SLC": ["Alaska", "Delta", "Southwest"],
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_watches(conn: sqlite3.Connection, cfg: Config, today: date) -> bool:
    """Invented trips, searches and deal watches, when the database has none yet."""
    if watches.list_trips(conn) or watches.list_searches(conn):
        return False
    home = cfg.home
    depart = today + timedelta(days=150)
    depart += timedelta(days=(5 - depart.weekday()) % 7)  # a Saturday
    watches.create_trip(
        conn,
        cfg,
        {
            "name": "Tokyo in spring",
            "slug": "tokyo-spring",
            "origin": home,
            "destination": "HND",
            "depart_date": depart.isoformat(),
            "return_date": (depart + timedelta(days=14)).isoformat(),
            "target_price": 80000,
        },
        today=today,
        expand=False,
    )
    watches.create_search(
        conn,
        cfg,
        {
            "name": "Ski weekends",
            "slug": "ski-weekends",
            "origin": home,
            "destinations": ["SLC"],
            "depart_dows": ["FRI"],
            "nights": 3,
            "target_price": 14000,
            "priority": 1,
        },
        today=today,
        expand=False,
    )
    watches.create_search(
        conn,
        cfg,
        {
            "name": "A week in Tokyo",
            "slug": "tokyo-week",
            "origin": home,
            "destinations": ["HND", "NRT"],
            "depart_dows": ["SAT"],
            "nights": 7,
            "target_price": 65000,
            "priority": 1,
        },
        today=today,
        expand=False,
    )
    for name, dest, cents in (
        ("Europe from Seattle", "europe", 60000),
        ("Japan from anywhere US", "japan", 70000),
    ):
        watches.create_deal_watch(
            conn,
            {
                "name": name,
                "origins": ["Seattle", "SEA", "West Coast", "US cities", "U.S. cities"],
                "destinations": [dest],
                "max_price": cents,
            },
        )
    return True


def seed(
    conn: sqlite3.Connection,
    cfg: Config,
    days: int = 45,
    today: date | None = None,
    rng_seed: int = 7,
) -> dict[str, int]:
    """Expand the grid (inventing watches if there are none), then fabricate `days` of
    daily observations."""
    rng = random.Random(rng_seed)
    today = today or date.today()
    seed_watches(conn, cfg, today)
    expand_mod.expand(conn, cfg, today=today, build_links=True)

    rows = conn.execute(
        "SELECT q.id, q.depart_date, q.return_date, q.pattern, r.destination, r.priority "
        "FROM queries q JOIN routes r ON r.id = q.route_id WHERE q.active = 1"
    ).fetchall()

    now = datetime.combine(today, datetime.min.time(), UTC).replace(hour=10)
    written = 0
    for q in rows:
        dest = q["destination"]
        base = BASE_PRICE.get(dest, 50000)
        depart = date.fromisoformat(q["depart_date"])
        # Holidays and long trips cost more; a per-query offset keeps rows distinct.
        seasonal = (
            1.0 + 0.18 * (1 if depart.month in (12, 1, 3, 4) else 0) + rng.uniform(-0.08, 0.12)
        )
        if q["return_date"]:
            nights = (date.fromisoformat(q["return_date"]) - depart).days
            seasonal += 0.02 * max(0, nights - 4)
        level = base * seasonal
        # A slow drift plus a couple of "sale" dips so alerts and lows exist.
        drift = rng.uniform(-0.004, 0.006)
        dips = {rng.randrange(days): rng.uniform(0.75, 0.88) for _ in range(rng.randrange(0, 3))}
        step = 1 if q["priority"] == 1 else 3
        for d in range(days, -1, -step):
            when = now - timedelta(days=d)
            days_out = (depart - when.date()).days
            if days_out < 0:
                continue
            last_minute = 1.0 + max(0.0, (21 - days_out)) * 0.012
            noise = 1.0 + rng.gauss(0, 0.035)
            factor = (1 + drift) ** (days - d) * last_minute * noise * dips.get(d, 1.0)
            price = round(level * factor / 100.0) * 100
            airline = rng.choice(AIRLINES.get(dest, ["Alaska"]))
            stops = 0 if rng.random() < 0.6 else 1
            for i, extra in enumerate((0, rng.randint(1500, 6000), rng.randint(6000, 14000))):
                conn.execute(
                    "INSERT INTO observations (query_id, observed_at, price_cents, currency, "
                    "airline, stops, duration_min, is_best, raw, source) "
                    "VALUES (?, ?, ?, 'USD', ?, ?, ?, ?, ?, 'demo')",
                    (
                        q["id"],
                        _iso(when),
                        price + extra,
                        airline if i == 0 else rng.choice(AIRLINES.get(dest, ["Delta"])),
                        stops if i == 0 else i,
                        150 if dest == "SLC" else 640 + 60 * stops,
                        1 if i == 0 else 0,
                        json.dumps({"synthetic": True}),
                    ),
                )
                written += 1
        conn.execute("UPDATE queries SET last_fetch_at = ? WHERE id = ?", (_iso(now), q["id"]))

    # A few calendar-sweep context rows so the sweep column shows.
    for q in rows[: max(1, len(rows) // 4)]:
        conn.execute(
            "INSERT INTO observations (query_id, observed_at, price_cents, currency, is_best, "
            "raw, source) "
            "VALUES (?, ?, ?, 'USD', 0, '{}', 'google_calendar')",
            (
                q["id"],
                _iso(now),
                int(BASE_PRICE.get(q["destination"], 50000) * rng.uniform(0.8, 1.1) / 100) * 100,
            ),
        )

    # Run history and request log, so the collector panel has something to show.
    for d in range(min(days, 14), -1, -1):
        started = now - timedelta(days=d)
        attempted = 60
        failed = rng.choice([0, 0, 0, 1, 2])
        conn.execute(
            "INSERT INTO run_log (started_at, finished_at, attempted, succeeded, failed, "
            "blocked, notes, kind) VALUES (?, ?, ?, ?, ?, 0, NULL, 'fetch')",
            (
                _iso(started),
                _iso(started + timedelta(minutes=7)),
                attempted,
                attempted - failed,
                failed,
            ),
        )
        for i in range(attempted):
            outcome = "ok" if i >= failed else rng.choice(["network", "layout"])
            conn.execute(
                "INSERT INTO fetch_attempts (run_id, query_id, at, source, outcome, latency_ms, "
                "error) VALUES (NULL, NULL, ?, 'google_html', ?, ?, NULL)",
                (_iso(started + timedelta(seconds=6 * i)), outcome, rng.randint(900, 2600)),
            )
    set_state(conn, "last_success_at", _iso(now))

    # Deal posts matching the shipped watches.
    posts = [
        (
            "Seattle to Paris, France for only $438 roundtrip",
            "Europe from Seattle",
            43800,
            "https://example.com/deal/paris",
        ),
        (
            "Many US cities to Tokyo, Japan from $612 roundtrip",
            "Japan from anywhere US",
            61200,
            "https://example.com/deal/tokyo",
        ),
        (
            "West Coast to Lisbon, Portugal from $497 roundtrip",
            "Europe from Seattle",
            49700,
            "https://example.com/deal/lisbon",
        ),
    ]
    for i, (title, watch, cents, link) in enumerate(posts):
        conn.execute(
            "INSERT OR IGNORE INTO deal_posts (guid, feed, title, link, published_at, seen_at, "
            "price_cents, watch, alerted_at) "
            "VALUES (?, 'demo', ?, ?, ?, ?, ?, ?, ?)",
            (
                f"demo-{i}",
                title,
                link,
                _iso(now - timedelta(days=i)),
                _iso(now - timedelta(days=i)),
                cents,
                watch,
                _iso(now - timedelta(days=i)),
            ),
        )
    _seed_alerts(conn, now)
    _seed_explore(conn, cfg, now, today, rng)
    conn.commit()
    return {"queries": len(rows), "observations": written, "days": days}


# Invented places for the built-in Explore boards (place ids say /demo/, so nothing
# here can be mistaken for a fare Google returned).
EXPLORE_PLACES = (
    ("Los Angeles", "United States", "LAX", 15000),
    ("Denver", "United States", "DEN", 17000),
    ("New York", "United States", "JFK", 24000),
    ("Mexico City", "Mexico", "MEX", 29000),
    ("Honolulu", "United States", "HNL", 38000),
    ("Reykjavík", "Iceland", "KEF", 49000),
    ("Lisbon", "Portugal", "LIS", 61000),
    ("Tokyo", "Japan", "HND", 72000),
)


def _seed_explore(
    conn: sqlite3.Connection, cfg: Config, now: datetime, today: date, rng: random.Random
) -> None:
    """A few fares on each built-in Explore board's first departures, seen hours ago."""
    from . import explore

    for board in explore.boards(conn, cfg):
        if board.watch_id is not None:
            continue
        longer = 1.0 + 0.04 * (board.nights or 0)
        for depart in explore.board_departures(cfg, board, today)[:4]:
            for name, country, airport, base in EXPLORE_PLACES:
                if rng.random() < 0.35:
                    continue  # like the real call: some places have no fare
                cents = round(base * longer * rng.uniform(0.85, 1.25) / 100) * 100
                conn.execute(
                    """INSERT INTO explore_prices (origin, region, place_id, name, country,
                           airport, depart_date, nights, price_cents, airline, stops,
                           duration_min, observed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Alaska', ?, ?, ?)""",
                    (
                        board.origin,
                        board.region,
                        f"/demo/{airport.lower()}",
                        name,
                        country,
                        airport,
                        depart,
                        board.nights,
                        cents,
                        0 if base < 30000 else 1,
                        120 + base // 100,
                        _iso(now - timedelta(hours=rng.randint(1, 20))),
                    ),
                )


def _money(cents: int) -> str:
    return f"${cents // 100:,}"


def _seed_alerts(conn: sqlite3.Connection, now: datetime) -> None:
    """A price alert for each trip's lowest price and one for the newest deal post, as the
    worker records them (`alerts_sent` plus a `notifications` row), so the app has recent
    alerts to show."""
    for trip in watches.list_trips(conn, status="active"):
        best = conn.execute(
            "SELECT o.id, o.query_id, o.observed_at, o.price_cents FROM observations o "
            "JOIN watch_queries w ON w.query_id = o.query_id "
            "WHERE w.trip_id = ? AND o.is_best = 1 AND o.source = 'demo' "
            "ORDER BY o.price_cents, o.observed_at LIMIT 1",
            (trip.id,),
        ).fetchone()
        if best is None:
            continue
        conn.execute(
            "INSERT INTO alerts_sent (query_id, observation_id, sent_at, reason, price_cents) "
            "VALUES (?, ?, ?, 'all_time_low', ?)",
            (best["query_id"], best["id"], best["observed_at"], best["price_cents"]),
        )
        dates = date.fromisoformat(trip.depart_date).strftime("%b %-d")
        if trip.return_date:
            dates += " – " + date.fromisoformat(trip.return_date).strftime("%b %-d")
        conn.execute(
            "INSERT INTO notifications (kind, title, body, url, created_at) "
            "VALUES ('price', ?, ?, ?, ?)",
            (
                f"{_money(best['price_cents'])} {trip.name} · {dates}",
                "the lowest price seen for this trip (synthetic)",
                f"/trip/{trip.slug}",
                best["observed_at"],
            ),
        )
    deal = conn.execute(
        "SELECT title, watch, price_cents, seen_at FROM deal_posts "
        "WHERE watch IS NOT NULL ORDER BY seen_at DESC LIMIT 1"
    ).fetchone()
    if deal is not None:
        conn.execute(
            "INSERT INTO notifications (kind, title, body, url, created_at) "
            "VALUES ('deal', ?, ?, '/deals', ?)",
            (deal["title"], f"{deal['watch']} · {_money(deal['price_cents'])}", deal["seen_at"]),
        )
