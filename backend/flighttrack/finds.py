"""The deal finder (deal finder track, D3): fares the app finds itself, confirmed before
anyone is told.

  1. **Usual price.** For a place, trip length and travel month, the median of its daily
     lowest Explore fares over `finds.usual_days`. It needs `finds.min_history_days` days
     of fares before anything is called "under its usual".
  2. **Candidates** (after each Explore slot, `find_candidates`). A board fare becomes a
     candidate when it is `finds.under_usual_pct` under the usual price, at or under the
     region watch's price, or `finds.new_low_pct` under the lowest fare seen before. While
     a place has too little history, each board's `finds.cheapest_per_board` cheapest
     places are candidates instead, and say so.
  3. **Confirmation** (in the detail lane, ahead of the rotation, `check`). One results
     page for the exact dates, with the owner's bag and fare filters. The find is
     confirmed when the real fare is within `finds.confirm_slack_pct` of the estimate (the
     estimate includes basic economy) or at or under the watch's price; otherwise it is
     gone, with the real fare in its note. Nothing is called a deal on an estimate alone.
  4. **Rechecks.** A confirmed find is searched again every `finds.recheck_hours` until its
     fare rises past the limit, the flights go or the date passes. One `deal` notification
     per find, and again only when it drops `finds.renotify_drop_pct` further.

Every request is a detail request (`fetch_attempts`, with no date pair), so it spends the
detail lane's share of the pace; a block starts the shared cooldown.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import explore as explore_mod
from . import fetch as fetch_mod
from .config import Config, Route
from .notifier import Notifier
from .report import fmt_money
from .source import BlockedError, NoResults, Source, SourceError, deep_link
from .store.db import iso, utcnow
from .store.db import now as clock_now

log = logging.getLogger(__name__)

MAX_FAILED_CHECKS = 3
REOPEN_DAYS = 7  # a gone find may come back as a candidate after this long


def find_key(origin: str, airport: str, depart: str, nights: int | None) -> str:
    return f"{origin}|{airport}|{depart}|{'ow' if nights is None else nights}"


def return_date(depart: str, nights: int | None) -> str | None:
    return (date.fromisoformat(depart) + timedelta(days=nights)).isoformat() if nights else None


def _money(cents: int) -> str:
    return fmt_money(cents)


# ---------------------------------------------------------------------------
# The usual price
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Usual:
    cents: int | None  # None until there are min_history_days days of fares
    lowest: int | None  # the lowest daily low seen before
    days: int  # days with a fare


def usual_price(
    conn: sqlite3.Connection,
    cfg: Config,
    origin: str,
    place_id: str,
    nights: int | None,
    month: int,
    before: str,
    now: datetime,
) -> Usual:
    """The median of the place's daily lows for this trip length and travel month, over
    `finds.usual_days`, counting only fares seen before `before` (so a new fare is judged
    against what came earlier)."""
    rows = conn.execute(
        """
        SELECT substr(observed_at, 1, 10) AS day, MIN(price_cents) AS low
        FROM explore_prices
        WHERE origin = ? AND place_id = ? AND nights IS ?
          AND CAST(substr(depart_date, 6, 2) AS INTEGER) = ?
          AND observed_at >= ? AND observed_at < ?
        GROUP BY day
        """,
        (
            origin,
            place_id,
            nights,
            month,
            iso(now - timedelta(days=cfg.finds.usual_days)),
            before,
        ),
    ).fetchall()
    lows = [int(r["low"]) for r in rows]
    if not lows:
        return Usual(None, None, 0)
    usual = int(statistics.median(lows)) if len(lows) >= cfg.finds.min_history_days else None
    return Usual(usual, min(lows), len(lows))


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def _why(
    cfg: Config,
    cents: int,
    usual: Usual,
    board: explore_mod.Board,
    rank: int,
) -> str | None:
    f = cfg.finds
    if board.max_price is not None and cents <= board.max_price:
        return f"under your {_money(board.max_price)}"
    if usual.cents and cents <= usual.cents * (1 - f.under_usual_pct / 100):
        pct = (usual.cents - cents) / usual.cents * 100
        return f"{pct:.0f}% under its usual {_money(usual.cents)}"
    if usual.lowest and usual.days >= 3 and cents <= usual.lowest * (1 - f.new_low_pct / 100):
        pct = (usual.lowest - cents) / usual.lowest * 100
        return f"{pct:.0f}% under the lowest seen before ({_money(usual.lowest)})"
    if usual.cents is None and rank < f.cheapest_per_board:
        return (
            f"one of the cheapest on “{board.title}” (too little history to say what is usual yet)"
        )
    return None


def find_candidates(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> int:
    """Look over every Explore board for fares worth confirming. Returns how many
    candidates are new (or came back)."""
    if not cfg.finds.enabled or not cfg.explore.enabled:
        return 0
    now = now or clock_now()
    stamp = iso(now)
    new = 0
    for board in explore_mod.boards(conn, cfg):
        view = explore_mod.view(conn, cfg, board, now)
        for rank, p in enumerate(view.places):
            if not p.airport:
                continue
            usual = usual_price(
                conn,
                cfg,
                board.origin,
                p.place_id,
                board.nights,
                int(p.depart_date[5:7]),
                p.observed_at,
                now,
            )
            why = _why(cfg, p.price_cents, usual, board, rank)
            if why is None:
                continue
            key = find_key(board.origin, p.airport, p.depart_date, board.nights)
            row = conn.execute("SELECT * FROM finds WHERE key = ?", (key,)).fetchone()
            values = (
                p.place_id,
                p.name,
                p.country,
                board.key,
                p.price_cents,
                usual.cents,
                why,
                board.max_price if why.startswith("under your") else None,
                stamp,
            )
            if row is None:
                conn.execute(
                    """INSERT INTO finds (key, origin, airport, depart_date, nights, place_id,
                           name, country, board_key, estimate_cents, usual_cents, why,
                           max_cents, found_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, board.origin, p.airport, p.depart_date, board.nights, *values),
                )
                new += 1
            elif row["status"] == "gone" and (
                p.price_cents <= row["estimate_cents"] * (1 - cfg.finds.new_low_pct / 100)
                or row["found_at"] < iso(now - timedelta(days=REOPEN_DAYS))
            ):
                conn.execute(
                    """UPDATE finds SET status = 'candidate', place_id = ?, name = ?, country = ?,
                           board_key = ?, estimate_cents = ?, usual_cents = ?, why = ?,
                           max_cents = ?, found_at = ?, checks = 0, note = NULL
                       WHERE id = ?""",
                    (*values, row["id"]),
                )
                new += 1
    conn.commit()
    if new:
        log.info("finds: %d new candidate(s)", new)
    return new


def due(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> list[sqlite3.Row]:
    """Finds to search now: candidates (oldest first), then confirmed finds whose last
    check is `finds.recheck_hours` old. Finds whose date has come are closed first."""
    if not cfg.finds.enabled:
        return []
    now = now or clock_now()
    today = now.date().isoformat()
    conn.execute(
        "UPDATE finds SET status = 'gone', note = 'the date has passed' "
        "WHERE status IN ('candidate', 'confirmed') AND depart_date <= ?",
        (today,),
    )
    conn.commit()
    recheck = iso(now - timedelta(hours=cfg.finds.recheck_hours))
    return conn.execute(
        """
        SELECT * FROM finds
        WHERE (status = 'candidate')
           OR (status = 'confirmed' AND (checked_at IS NULL OR checked_at < ?))
        ORDER BY status = 'confirmed', COALESCE(checked_at, found_at), id
        """,
        (recheck,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Checking against the results page
# ---------------------------------------------------------------------------


@dataclass
class CheckStats:
    requests: int = 0
    confirmed: int = 0
    gone: int = 0
    failed: int = 0
    blocked: bool = False
    notified: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = (
            f"checked={self.requests} confirmed={self.confirmed} gone={self.gone} "
            f"failed={self.failed} notified={self.notified}"
        )
        return s + (" ABORTED=blocked" if self.blocked else "")


def limit_for(cfg: Config, row: sqlite3.Row) -> int:
    """The highest real fare that still confirms a find."""
    slack = round(int(row["estimate_cents"]) * (1 + cfg.finds.confirm_slack_pct / 100))
    return max(slack, int(row["max_cents"] or 0))


def _notify(
    conn: sqlite3.Connection, notifier: Notifier, row: sqlite3.Row, cents: int, now: datetime
) -> bool:
    when = return_date(row["depart_date"], row["nights"])
    dates = row["depart_date"] + (f" – {when}" if when else ", one way")
    title = f"✈ {_money(cents)} {row['name']} · {dates}"
    body = f"Found: {row['why']}. From {row['origin']}, checked with your bag and fare filters."

    def remember(c: sqlite3.Connection) -> None:
        c.execute("UPDATE finds SET notified_cents = ? WHERE id = ?", (cents, row["id"]))

    notifier.send(conn, "deal", title, body, "/deals", record=remember)
    log.info("finds: notified %s", title)
    return True


def check(
    conn: sqlite3.Connection,
    cfg: Config,
    source: Source,
    rows: list[sqlite3.Row],
    notifier: Notifier,
    now: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> CheckStats:
    """Search each find's exact dates on the results page and confirm, keep, or close it."""
    stats = CheckStats()
    now = now or clock_now()
    stamp = iso(now)
    cur = conn.execute("INSERT INTO run_log (started_at, kind) VALUES (?, 'finds')", (utcnow(),))
    run_id = cur.lastrowid
    conn.commit()
    for i, row in enumerate(rows):
        if i:
            sleeper(random.uniform(cfg.fetch.min_sleep_seconds, cfg.fetch.max_sleep_seconds))
        ret = return_date(row["depart_date"], row["nights"])
        route = Route(row["airport"], row["name"], None, 2, None, origin=row["origin"])
        stats.requests += 1
        t0 = time.monotonic()

        def attempt(outcome: str, error: str | None = None, t0: float = t0) -> None:
            name = str(getattr(source, "last_used", None) or getattr(source, "name", "?"))
            latency = int((time.monotonic() - t0) * 1000)
            fetch_mod.log_attempt(conn, run_id, None, name, outcome, latency, error)

        try:
            offers = source.fetch(cfg, route, row["depart_date"], ret, cfg.fetch.offers_per_fetch)
        except NoResults as exc:
            attempt("empty", str(exc))
            stats.gone += 1
            conn.execute(
                "UPDATE finds SET status = 'gone', checked_at = ?, checks = 0, "
                "note = 'no flights on these dates any more' WHERE id = ?",
                (stamp, row["id"]),
            )
            conn.commit()
            continue
        except BlockedError as exc:
            attempt("blocked", str(exc))
            stats.blocked = True
            stats.errors.append(f"{row['key']}: blocked")
            fetch_mod.enter_cooldown(conn, cfg, now)
            conn.commit()
            break
        except SourceError as exc:
            attempt(exc.kind, str(exc))
            stats.failed += 1
            checks = int(row["checks"]) + 1
            gone = checks >= MAX_FAILED_CHECKS
            conn.execute(
                "UPDATE finds SET checks = ?, note = ?, status = CASE WHEN ? THEN 'gone' "
                "ELSE status END WHERE id = ?",
                (
                    checks,
                    f"could not be checked: {exc.kind}" if gone else f"last check: {exc.kind}",
                    gone,
                    row["id"],
                ),
            )
            conn.commit()
            continue
        attempt("ok")
        best = min(offers, key=lambda o: o.price_cents)
        if best.price_cents <= limit_for(cfg, row):
            first = row["status"] == "candidate"
            stats.confirmed += 1 if first else 0
            conn.execute(
                """UPDATE finds SET status = 'confirmed', price_cents = ?, airline = ?, stops = ?,
                       duration_min = ?, checked_at = ?, confirmed_at = COALESCE(confirmed_at, ?),
                       checks = 0, note = NULL
                   WHERE id = ?""",
                (
                    best.price_cents,
                    best.airline,
                    best.stops,
                    best.duration_min,
                    stamp,
                    stamp,
                    row["id"],
                ),
            )
            conn.commit()
            last = row["notified_cents"]
            if last is None or best.price_cents <= last * (1 - cfg.finds.renotify_drop_pct / 100):
                stats.notified += _notify(conn, notifier, row, best.price_cents, now)
        else:
            stats.gone += 1
            conn.execute(
                "UPDATE finds SET status = 'gone', price_cents = ?, checked_at = ?, checks = 0, "
                "note = ? WHERE id = ?",
                (
                    best.price_cents,
                    stamp,
                    f"the real fare with your bag and fare filters is {_money(best.price_cents)} "
                    f"(the estimate was {_money(row['estimate_cents'])})",
                    row["id"],
                ),
            )
            conn.commit()
    conn.execute(
        "UPDATE run_log SET finished_at = ?, attempted = ?, succeeded = ?, failed = ?, "
        "blocked = ? WHERE id = ?",
        (
            utcnow(),
            stats.requests,
            stats.requests - stats.failed - (1 if stats.blocked else 0),
            stats.failed + (1 if stats.blocked else 0),
            1 if stats.blocked else 0,
            run_id,
        ),
    )
    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Reading finds back
# ---------------------------------------------------------------------------


def listing(
    conn: sqlite3.Connection, cfg: Config, now: datetime, *, confirmed_only: bool = False
) -> list[dict[str, object]]:
    """Open finds, confirmed first (biggest saving first), then candidates being checked."""
    today = now.date().isoformat()
    statuses = ("confirmed",) if confirmed_only else ("confirmed", "candidate")
    rows = conn.execute(
        f"""SELECT * FROM finds WHERE status IN ({",".join("?" * len(statuses))})
            AND depart_date > ?""",
        (*statuses, today),
    ).fetchall()

    def saving(r: sqlite3.Row) -> float:
        price = r["price_cents"] or r["estimate_cents"]
        return (r["usual_cents"] - price) / r["usual_cents"] if r["usual_cents"] else 0.0

    rows.sort(key=lambda r: (r["status"] != "confirmed", -saving(r), r["price_cents"] or 0))
    out = []
    for r in rows:
        ret = return_date(r["depart_date"], r["nights"])
        route = Route(r["airport"], r["name"], None, 2, None, origin=r["origin"])
        out.append(
            {
                **{k: r[k] for k in r.keys()},  # noqa: SIM118 - sqlite3.Row has no items()
                "return_date": ret,
                "link": deep_link(cfg, route, r["depart_date"], ret),
            }
        )
    return out
