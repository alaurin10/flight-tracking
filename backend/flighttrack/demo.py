"""Seed a database with plausible synthetic history, so the report can be seen
before a single real price has been collected.

Deterministic (seeded), clearly synthetic (source='demo'), never written to
the real database path unless you point it there.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import expand as expand_mod
from .config import Config
from .db import set_state, utcnow

BASE_PRICE = {"HND": 92000, "NRT": 84000, "SLC": 21000}
AIRLINES = {"HND": ["Alaska", "ANA", "Japan Airlines", "Delta"], "NRT": ["ZIPAIR", "Japan Airlines", "United"],
            "SLC": ["Alaska", "Delta", "Southwest"]}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(conn: sqlite3.Connection, cfg: Config, days: int = 45, today: date | None = None, rng_seed: int = 7) -> dict:
    """Expand the grid for `cfg`, then fabricate `days` of daily observations."""
    rng = random.Random(rng_seed)
    today = today or date.today()
    expand_mod.expand(conn, cfg, today=today, build_links=True)

    rows = conn.execute(
        "SELECT q.id, q.depart_date, q.return_date, q.pattern, r.destination, r.priority "
        "FROM queries q JOIN routes r ON r.id = q.route_id WHERE q.active = 1"
    ).fetchall()

    now = datetime.combine(today, datetime.min.time(), timezone.utc).replace(hour=10)
    written = 0
    for q in rows:
        dest = q["destination"]
        base = BASE_PRICE.get(dest, 50000)
        depart = date.fromisoformat(q["depart_date"])
        # Holidays and long trips cost more; a per-query offset keeps rows distinct.
        seasonal = 1.0 + 0.18 * (1 if depart.month in (12, 1, 3, 4) else 0) + rng.uniform(-0.08, 0.12)
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
            price = int(round(level * factor / 100.0)) * 100
            airline = rng.choice(AIRLINES.get(dest, ["Alaska"]))
            stops = 0 if rng.random() < 0.6 else 1
            for i, extra in enumerate((0, rng.randint(1500, 6000), rng.randint(6000, 14000))):
                conn.execute(
                    "INSERT INTO observations (query_id, observed_at, price_cents, currency, airline, stops, duration_min, is_best, raw, source) "
                    "VALUES (?, ?, ?, 'USD', ?, ?, ?, ?, ?, 'demo')",
                    (q["id"], _iso(when), price + extra, airline if i == 0 else rng.choice(AIRLINES.get(dest, ["Delta"])),
                     stops if i == 0 else i, 150 if dest == "SLC" else 640 + 60 * stops, 1 if i == 0 else 0,
                     json.dumps({"synthetic": True})),
                )
                written += 1
        conn.execute("UPDATE queries SET last_fetch_at = ? WHERE id = ?", (_iso(now), q["id"]))

    # A few calendar-sweep context rows so the sweep column shows.
    for q in rows[: max(1, len(rows) // 4)]:
        conn.execute(
            "INSERT INTO observations (query_id, observed_at, price_cents, currency, is_best, raw, source) "
            "VALUES (?, ?, ?, 'USD', 0, '{}', 'google_calendar')",
            (q["id"], _iso(now), int(BASE_PRICE.get(q["destination"], 50000) * rng.uniform(0.8, 1.1) / 100) * 100),
        )

    # Run history and request log, so the collector panel has something to show.
    for d in range(min(days, 14), -1, -1):
        started = now - timedelta(days=d)
        attempted = 60
        failed = rng.choice([0, 0, 0, 1, 2])
        conn.execute(
            "INSERT INTO run_log (started_at, finished_at, attempted, succeeded, failed, blocked, notes, kind) VALUES (?, ?, ?, ?, ?, 0, NULL, 'fetch')",
            (_iso(started), _iso(started + timedelta(minutes=7)), attempted, attempted - failed, failed),
        )
        for i in range(attempted):
            outcome = "ok" if i >= failed else rng.choice(["network", "layout"])
            conn.execute(
                "INSERT INTO fetch_attempts (run_id, query_id, at, source, outcome, latency_ms, error) VALUES (NULL, NULL, ?, 'google_html', ?, ?, NULL)",
                (_iso(started + timedelta(seconds=6 * i)), outcome, rng.randint(900, 2600)),
            )
    set_state(conn, "last_success_at", _iso(now))

    # Deal posts matching the shipped watches.
    posts = [
        ("Seattle to Paris, France for only $438 roundtrip", "Europe from Seattle", 43800, "https://example.com/deal/paris"),
        ("Many US cities to Tokyo, Japan from $612 roundtrip", "Japan from anywhere US", 61200, "https://example.com/deal/tokyo"),
        ("West Coast to Lisbon, Portugal from $497 roundtrip", "Europe from Seattle", 49700, "https://example.com/deal/lisbon"),
    ]
    for i, (title, watch, cents, link) in enumerate(posts):
        conn.execute(
            "INSERT OR IGNORE INTO deal_posts (guid, feed, title, link, published_at, seen_at, price_cents, watch, alerted_at) "
            "VALUES (?, 'demo', ?, ?, ?, ?, ?, ?, ?)",
            (f"demo-{i}", title, link, _iso(now - timedelta(days=i)), _iso(now - timedelta(days=i)), cents, watch, _iso(now - timedelta(days=i))),
        )
    conn.commit()
    return {"queries": len(rows), "observations": written, "days": days}
