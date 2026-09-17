"""Turn date patterns and fixed trips in config into concrete rows in `queries`.

Idempotent by construction: re-running after a config change adds what is new,
refreshes what changed, and deactivates what no longer applies. It never
deletes, because every row is the anchor for an observation history that is the
entire point of the system.

No network. This module is pure date arithmetic plus deep-link construction.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from .config import DOW, Config, Pattern

# Beyond this Google rarely has inventory; an explicit window is clipped here
# so a typo in `window.end` cannot generate a year of empty fetches.
MAX_WINDOW_DAYS = 365


@dataclass
class ExpansionResult:
    routes_upserted: int = 0
    routes_deactivated: int = 0
    queries_inserted: int = 0
    queries_expired: int = 0
    queries_reactivated: int = 0
    links_refreshed: int = 0
    total_active: int = 0
    trips: int = 0

    def summary(self) -> str:
        return (
            f"routes: {self.routes_upserted} active, {self.routes_deactivated} deactivated | "
            f"queries: +{self.queries_inserted} new, {self.queries_expired} expired, "
            f"{self.queries_reactivated} reactivated, {self.links_refreshed} links refreshed | "
            f"{self.total_active} active total ({self.trips} fixed trips)"
        )


def sync_routes(conn: sqlite3.Connection, cfg: Config) -> tuple[int, int]:
    """Make the `routes` table match config. Dropped routes are deactivated, never deleted."""
    configured = set()
    for r in cfg.routes:
        configured.add((cfg.home, r.dest))
        conn.execute(
            """
            INSERT INTO routes (origin, destination, label, target_price, priority, max_stops, active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(origin, destination) DO UPDATE SET
                label        = excluded.label,
                target_price = excluded.target_price,
                priority     = excluded.priority,
                max_stops    = excluded.max_stops,
                active       = 1
            """,
            (cfg.home, r.dest, r.label, r.target_price, r.priority, r.max_stops),
        )

    deactivated = 0
    for row in conn.execute("SELECT id, origin, destination FROM routes WHERE active = 1"):
        if (row["origin"], row["destination"]) not in configured:
            conn.execute("UPDATE routes SET active = 0 WHERE id = ?", (row["id"],))
            deactivated += 1

    return len(configured), deactivated


def _weekday_dates(start: date, end: date, dow_index: int) -> list[date]:
    offset = (dow_index - start.weekday()) % 7
    cursor = start + timedelta(days=offset)
    out: list[date] = []
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=7)
    return out


def departure_dates(cfg: Config, pattern: Pattern | int, today: date | None = None) -> list[date]:
    """Every departure date a pattern produces inside its effective window.

    Accepts a bare weekday index for backwards compatibility. The effective
    window is the horizon, narrowed by the pattern's own `window` and its
    `min_days_ahead` override; `window.end` may extend past the horizon.
    """
    today = today or date.today()
    if isinstance(pattern, int):
        pattern = Pattern("_", next(k for k, v in DOW.items() if v == pattern), 0)

    lo = cfg.min_days_ahead if pattern.min_days_ahead is None else pattern.min_days_ahead
    start = today + timedelta(days=lo)
    end = today + timedelta(days=cfg.max_days_ahead)
    if pattern.window_start:
        start = max(start, date.fromisoformat(pattern.window_start))
    if pattern.window_end:
        end = min(date.fromisoformat(pattern.window_end), today + timedelta(days=MAX_WINDOW_DAYS))
    if end < start:
        return []

    out: set[date] = set()
    for dow in pattern.dow_indexes:
        out.update(_weekday_dates(start, end, dow))
    return sorted(out)


def _upsert_query(conn, result, route_id, depart_s, ret_s, pattern_name, link) -> None:
    cur = conn.execute(
        """
        INSERT INTO queries (route_id, depart_date, return_date, pattern, active, deep_link)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(route_id, depart_date, return_date) DO NOTHING
        """,
        (route_id, depart_s, ret_s, pattern_name, link),
    )
    if cur.rowcount:
        result.queries_inserted += 1
        return
    # Existing row: make sure it is active (a window may have re-included it)
    # and refresh the link only when it actually moved.
    upd = conn.execute(
        """
        UPDATE queries SET active = 1
        WHERE route_id = ? AND depart_date = ? AND return_date IS ? AND active = 0 AND depart_date >= ?
        """,
        (route_id, depart_s, ret_s, depart_s),
    )
    result.queries_reactivated += upd.rowcount
    if link is not None:
        upd = conn.execute(
            """
            UPDATE queries SET deep_link = ?
            WHERE route_id = ? AND depart_date = ? AND return_date IS ?
              AND (deep_link IS NULL OR deep_link != ?)
            """,
            (link, route_id, depart_s, ret_s, link),
        )
        result.links_refreshed += upd.rowcount


def expand(
    conn: sqlite3.Connection,
    cfg: Config,
    today: date | None = None,
    build_links: bool = True,
) -> ExpansionResult:
    """Materialise the full date grid (patterns + fixed trips) into `queries`."""
    today = today or date.today()
    result = ExpansionResult()
    result.routes_upserted, result.routes_deactivated = sync_routes(conn, cfg)

    route_ids = {
        row["destination"]: row["id"]
        for row in conn.execute(
            "SELECT id, destination FROM routes WHERE origin = ? AND active = 1", (cfg.home,)
        )
    }

    link_for = None
    if build_links:
        from .source import deep_link

        link_for = deep_link

    def link(route, dep, ret):
        if link_for is None:
            return None
        try:
            return link_for(cfg, route, dep, ret)
        except Exception:
            return None

    wanted: set[tuple[int, str, str | None]] = set()

    for route in cfg.routes:
        route_id = route_ids.get(route.dest)
        if route_id is None:
            continue
        for pattern in cfg.patterns:
            if not pattern.applies_to(route.dest):
                continue
            for depart in departure_dates(cfg, pattern, today):
                ret = depart + timedelta(days=pattern.nights) if pattern.nights else None
                depart_s, ret_s = depart.isoformat(), ret.isoformat() if ret else None
                wanted.add((route_id, depart_s, ret_s))
                _upsert_query(conn, result, route_id, depart_s, ret_s, pattern.name, link(route, depart_s, ret_s))

    # Fixed trips: tracked right up to departure, whatever min_days_ahead says —
    # the question they answer is "should I book today", and today may be close.
    for trip in cfg.trips:
        route = cfg.route(trip.dest)
        route_id = route_ids.get(trip.dest)
        if route is None or route_id is None or trip.depart < today.isoformat():
            continue
        result.trips += 1
        wanted.add((route_id, trip.depart, trip.ret))
        _upsert_query(conn, result, route_id, trip.depart, trip.ret, trip.name, link(route, trip.depart, trip.ret))
        # A trip's own target overrides the route's for alerting; store it on the row.
        if trip.target_price is not None:
            conn.execute(
                "UPDATE queries SET pattern = ? WHERE route_id = ? AND depart_date = ? AND return_date IS ?",
                (trip.name, route_id, trip.depart, trip.ret),
            )

    # Prune departures that have already happened. active=0, never DELETE.
    cur = conn.execute(
        "UPDATE queries SET active = 0 WHERE active = 1 AND depart_date < ?",
        (today.isoformat(),),
    )
    result.queries_expired = cur.rowcount

    result.total_active = conn.execute(
        """
        SELECT COUNT(*) FROM queries q
        JOIN routes r ON r.id = q.route_id
        WHERE q.active = 1 AND r.active = 1
        """
    ).fetchone()[0]

    conn.commit()
    return result


def trip_targets(cfg: Config) -> dict[str, int]:
    """pattern/trip name → target cents, for trips that set their own."""
    return {t.name: t.target_price for t in cfg.trips if t.target_price is not None}
