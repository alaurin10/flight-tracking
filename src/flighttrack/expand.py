"""Turn date patterns in config into concrete rows in `queries`.

Idempotent by construction: re-running after a config change adds what is new,
refreshes what changed, and deactivates what no longer applies. It never
deletes, because every row is the anchor for an observation history that is the
entire point of the system.

No network. This module is pure date arithmetic plus protobuf URL construction.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from .config import Config


@dataclass
class ExpansionResult:
    routes_upserted: int = 0
    routes_deactivated: int = 0
    queries_inserted: int = 0
    queries_expired: int = 0
    links_refreshed: int = 0
    total_active: int = 0

    def summary(self) -> str:
        return (
            f"routes: {self.routes_upserted} active, {self.routes_deactivated} deactivated | "
            f"queries: +{self.queries_inserted} new, {self.queries_expired} expired, "
            f"{self.links_refreshed} links refreshed | {self.total_active} active total"
        )


def sync_routes(conn: sqlite3.Connection, cfg: Config) -> tuple[int, int]:
    """Make the `routes` table match config.

    Routes dropped from config are deactivated rather than deleted — their
    observations remain the historical baseline for any route that returns.
    """
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


def departure_dates(cfg: Config, dow_index: int, today: date | None = None) -> list[date]:
    """Every date inside the horizon falling on the given weekday."""
    today = today or date.today()
    start = today + timedelta(days=cfg.min_days_ahead)
    end = today + timedelta(days=cfg.max_days_ahead)

    # Advance from `start` to the first matching weekday, then step by weeks.
    offset = (dow_index - start.weekday()) % 7
    cursor = start + timedelta(days=offset)

    out: list[date] = []
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=7)
    return out


def expand(
    conn: sqlite3.Connection,
    cfg: Config,
    today: date | None = None,
    build_links: bool = True,
) -> ExpansionResult:
    """Materialise the full date grid into `queries`."""
    today = today or date.today()
    result = ExpansionResult()
    result.routes_upserted, result.routes_deactivated = sync_routes(conn, cfg)

    route_ids = {
        row["destination"]: row["id"]
        for row in conn.execute(
            "SELECT id, destination FROM routes WHERE origin = ? AND active = 1", (cfg.home,)
        )
    }

    # Deep links are derived from the search parameters, so a config change to
    # (say) exclude_basic_economy must refresh them. Imported lazily so that
    # expansion still works if the scraper dependency is unavailable.
    link_for = None
    if build_links:
        try:
            from .source import deep_link

            link_for = deep_link
        except Exception:  # pragma: no cover - dependency-specific
            link_for = None

    for route in cfg.routes:
        route_id = route_ids.get(route.dest)
        if route_id is None:
            continue

        for pattern in cfg.patterns:
            for depart in departure_dates(cfg, pattern.dow_index, today):
                ret = depart + timedelta(days=pattern.nights) if pattern.nights else None
                depart_s = depart.isoformat()
                ret_s = ret.isoformat() if ret else None

                link = None
                if link_for is not None:
                    try:
                        link = link_for(cfg, route, depart_s, ret_s)
                    except Exception:
                        link = None

                cur = conn.execute(
                    """
                    INSERT INTO queries (route_id, depart_date, return_date, pattern, active, deep_link)
                    VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(route_id, depart_date, return_date) DO NOTHING
                    """,
                    (route_id, depart_s, ret_s, pattern.name, link),
                )
                if cur.rowcount:
                    result.queries_inserted += 1
                elif link is not None:
                    # Existing row: refresh the link only when it actually moved.
                    upd = conn.execute(
                        """
                        UPDATE queries SET deep_link = ?
                        WHERE route_id = ? AND depart_date = ? AND return_date IS ?
                          AND (deep_link IS NULL OR deep_link != ?)
                        """,
                        (link, route_id, depart_s, ret_s, link),
                    )
                    result.links_refreshed += upd.rowcount

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
