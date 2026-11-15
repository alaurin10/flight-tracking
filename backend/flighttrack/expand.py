"""Turn the trips and searches in the database into rows in `routes` and `queries`.

Idempotent by construction: re-running after a change adds what is new,
refreshes what changed, and deactivates what is no longer wanted. It never
deletes a route or a query, because each is the anchor of a price history that
is the whole point of the system.

* Routes are derived: one per origin and destination that some active trip or
  search uses. A new route is labelled with its destination's city; an existing
  one keeps its label and its own target. Routes nothing uses are deactivated.
* Each active trip and search gets its date pairs in `queries` and a link in
  `watch_queries`. Links that no longer apply are removed, and a date pair with
  no links is deactivated. `queries.pattern` holds the slug of the trip or
  search that owns the pair (trips first), for the reports.

No network. This module is date arithmetic plus deep-link construction.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import airports
from .config import DOW, Config, Pattern, Route

# Beyond this Google rarely has inventory; an explicit window is clipped here
# so a typo in a window's end cannot generate a year of empty fetches.
MAX_WINDOW_DAYS = 365

Pair = tuple[str, str, str, str | None]  # origin, destination, depart, return
Owner = tuple[str, int, str]  # ("trip" | "search", id, slug)


@dataclass
class ExpansionResult:
    routes_upserted: int = 0
    routes_deactivated: int = 0
    queries_inserted: int = 0
    queries_expired: int = 0
    queries_reactivated: int = 0
    queries_unwanted: int = 0
    links_refreshed: int = 0
    links_added: int = 0
    links_removed: int = 0
    total_active: int = 0
    trips: int = 0
    searches: int = 0

    def summary(self) -> str:
        return (
            f"routes: {self.routes_upserted} active, {self.routes_deactivated} deactivated | "
            f"queries: +{self.queries_inserted} new, {self.queries_expired} expired, "
            f"{self.queries_unwanted} no longer wanted, {self.queries_reactivated} reactivated, "
            f"{self.links_refreshed} links refreshed | "
            f"{self.total_active} active total ({self.trips} fixed trips, "
            f"{self.searches} searches)"
        )


@dataclass
class _Want:
    """Everything the active trips and searches ask for."""

    pairs: dict[Pair, list[Owner]] = field(default_factory=dict)
    # (origin, destination) → (priority, max_stops values)
    routes: dict[tuple[str, str], tuple[int, list[int | None]]] = field(default_factory=dict)
    trips: int = 0
    searches: int = 0

    def add_route(self, origin: str, dest: str, priority: int, max_stops: int | None) -> None:
        prio, stops = self.routes.get((origin, dest), (9, []))
        self.routes[(origin, dest)] = (min(prio, priority), [*stops, max_stops])


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
    window is the horizon, narrowed by the pattern's own window and its
    `min_days_ahead` override; the window's end may extend past the horizon.
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


def search_pattern(row: sqlite3.Row) -> Pattern:
    """A `searches` row as the date-arithmetic shape `departure_dates` takes."""
    dows = tuple(json.loads(row["depart_dows"]))
    return Pattern(
        row["slug"],
        dows[0],
        row["nights"],
        dows,
        tuple(json.loads(row["destinations"])),
        row["window_start"],
        row["window_end"],
        row["min_days_ahead"],
    )


def _wanted(conn: sqlite3.Connection, cfg: Config, today: date) -> _Want:
    want = _Want()
    trips = conn.execute(
        "SELECT * FROM trips WHERE status = 'active' AND depart_date >= ? ORDER BY id",
        (today.isoformat(),),
    ).fetchall()
    # Fixed trips are tracked right up to departure, whatever min_days_ahead says: the
    # question they answer is "should I book today", and today may be close.
    for t in trips:
        want.trips += 1
        pair = (t["origin"], t["destination"], t["depart_date"], t["return_date"])
        want.pairs.setdefault(pair, []).append(("trip", t["id"], t["slug"]))
        want.add_route(t["origin"], t["destination"], 1, t["max_stops"])

    for s in conn.execute("SELECT * FROM searches WHERE status = 'active' ORDER BY id"):
        want.searches += 1
        pattern = search_pattern(s)
        dates = departure_dates(cfg, pattern, today)
        for dest in pattern.routes:
            want.add_route(s["origin"], dest, s["priority"], s["max_stops"])
            for depart in dates:
                ret = depart + timedelta(days=pattern.nights) if pattern.nights else None
                pair = (
                    s["origin"],
                    dest,
                    depart.isoformat(),
                    ret.isoformat() if ret else None,
                )
                want.pairs.setdefault(pair, []).append(("search", s["id"], s["slug"]))
    return want


def wanted_pairs(conn: sqlite3.Connection, cfg: Config, today: date | None = None) -> set[Pair]:
    """The date pairs the active trips and searches want, without writing anything."""
    return set(_wanted(conn, cfg, today or date.today()).pairs)


def _route_stops(values: list[int | None]) -> int | None:
    """Record everything unless every watch on the route limits stops."""
    if not values or any(v is None for v in values):
        return None
    return max(v for v in values if v is not None)


def sync_routes(conn: sqlite3.Connection, want: _Want) -> tuple[int, int]:
    """Make `routes` match what is wanted. Unused routes are deactivated, never deleted."""
    for (origin, dest), (priority, stops) in want.routes.items():
        conn.execute(
            """
            INSERT INTO routes
                (origin, destination, label, target_price, priority, max_stops, active)
            VALUES (?, ?, ?, NULL, ?, ?, 1)
            ON CONFLICT(origin, destination) DO UPDATE SET
                priority  = excluded.priority,
                max_stops = excluded.max_stops,
                active    = 1
            """,
            (origin, dest, airports.city(dest), priority, _route_stops(stops)),
        )
    deactivated = 0
    for row in conn.execute("SELECT id, origin, destination FROM routes WHERE active = 1"):
        if (row["origin"], row["destination"]) not in want.routes:
            conn.execute("UPDATE routes SET active = 0 WHERE id = ?", (row["id"],))
            deactivated += 1
    return len(want.routes), deactivated


def _upsert_query(
    conn: sqlite3.Connection,
    result: ExpansionResult,
    route_id: int,
    depart_s: str,
    ret_s: str | None,
    slug: str,
    link: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO queries (route_id, depart_date, return_date, pattern, active, deep_link)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(route_id, depart_date, return_date) DO NOTHING
        """,
        (route_id, depart_s, ret_s, slug, link),
    )
    if cur.rowcount:
        result.queries_inserted += 1
        return int(cur.lastrowid or 0)
    row = conn.execute(
        "SELECT id, active, pattern, deep_link FROM queries "
        "WHERE route_id = ? AND depart_date = ? AND return_date IS ?",
        (route_id, depart_s, ret_s),
    ).fetchone()
    if not row["active"]:
        conn.execute("UPDATE queries SET active = 1 WHERE id = ?", (row["id"],))
        result.queries_reactivated += 1
    if row["pattern"] != slug:
        conn.execute("UPDATE queries SET pattern = ? WHERE id = ?", (slug, row["id"]))
    if link is not None and row["deep_link"] != link:
        conn.execute("UPDATE queries SET deep_link = ? WHERE id = ?", (link, row["id"]))
        result.links_refreshed += 1
    return int(row["id"])


def expand(
    conn: sqlite3.Connection,
    cfg: Config,
    today: date | None = None,
    build_links: bool = True,
) -> ExpansionResult:
    """Materialise every active trip and search into routes, date pairs and links."""
    today = today or date.today()
    result = ExpansionResult()
    want = _wanted(conn, cfg, today)
    result.trips, result.searches = want.trips, want.searches
    result.routes_upserted, result.routes_deactivated = sync_routes(conn, want)

    routes: dict[tuple[str, str], tuple[int, Route]] = {}
    for row in conn.execute("SELECT * FROM routes WHERE active = 1"):
        routes[(row["origin"], row["destination"])] = (
            row["id"],
            Route(
                row["destination"],
                row["label"],
                row["target_price"],
                row["priority"],
                row["max_stops"],
                origin=row["origin"],
            ),
        )

    link_for: Callable[[Config, Route, str, str | None], str] | None = None
    if build_links:
        from .source import deep_link

        link_for = deep_link

    def link(route: Route, dep: str, ret: str | None) -> str | None:
        if link_for is None:
            return None
        try:
            return link_for(cfg, route, dep, ret)
        except Exception:
            return None

    desired: set[tuple[int, str, int]] = set()  # (query_id, kind, watch id)
    for (origin, dest, depart_s, ret_s), owners in want.pairs.items():
        route_id, route = routes[(origin, dest)]
        owners_sorted = sorted(owners, key=lambda o: (o[0] != "trip", o[1]))
        qid = _upsert_query(
            conn,
            result,
            route_id,
            depart_s,
            ret_s,
            owners_sorted[0][2],
            link(route, depart_s, ret_s),
        )
        for kind, watch_id, _ in owners:
            desired.add((qid, kind, watch_id))

    existing: set[tuple[int, str, int]] = set()
    for row in conn.execute("SELECT query_id, trip_id, search_id FROM watch_queries"):
        if row["trip_id"] is not None:
            existing.add((row["query_id"], "trip", row["trip_id"]))
        else:
            existing.add((row["query_id"], "search", row["search_id"]))
    for qid, kind, watch_id in desired - existing:
        conn.execute(
            f"INSERT INTO watch_queries (query_id, {kind}_id) VALUES (?, ?)", (qid, watch_id)
        )
        result.links_added += 1
    for qid, kind, watch_id in existing - desired:
        conn.execute(
            f"DELETE FROM watch_queries WHERE query_id = ? AND {kind}_id = ?", (qid, watch_id)
        )
        result.links_removed += 1

    # Departures that have already happened. active=0, never DELETE.
    cur = conn.execute(
        "UPDATE queries SET active = 0 WHERE active = 1 AND depart_date < ?",
        (today.isoformat(),),
    )
    result.queries_expired = cur.rowcount
    # Date pairs nothing wants any more keep their history but stop being fetched.
    cur = conn.execute(
        "UPDATE queries SET active = 0 WHERE active = 1 "
        "AND id NOT IN (SELECT query_id FROM watch_queries)"
    )
    result.queries_unwanted = cur.rowcount

    result.total_active = conn.execute(
        """
        SELECT COUNT(*) FROM queries q
        JOIN routes r ON r.id = q.route_id
        WHERE q.active = 1 AND r.active = 1
        """
    ).fetchone()[0]

    conn.commit()
    return result
