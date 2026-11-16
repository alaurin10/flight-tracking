"""Explore boards: whole regions priced with Google's Explore call (deal finder track, D2).

A board is a region (Anywhere, Europe, Asia, …) from an airport for a trip length,
priced for every week ahead: one Explore call per departure date, each run again when
it is `explore.refresh_hours` old (a `scans` row of kind `explore`). Boards come from:

  * the built-in "anywhere from home" boards (`explore.boards`, by trip length);
  * each active region watch whose destinations name a region Explore can ask for
    (`REGION_PLACES`), priced only in its months. A region Explore has no place for
    (Japan, Hawaii, Mexico, the Caribbean) is read out of a wider one by the region's
    words (`deals.REGIONS`) matched against each destination's name and country.

Each call returns 75–90 places and a fare for some of them: the fares Google already
has, so a different handful each time (live, SEA→Anywhere: 6 to 8 of 92). The board
fills in as its departures are priced again. Fares are stored in `explore_prices`,
append-only, and a board shows each place's cheapest current fare over its departures.
Like calendar prices they include basic economy, so they are estimates; a place worth
a look gets a detail fetch in D3.

Explore requests are the `explore` lane (`fetch_attempts.source = 'google_explore'`).
A block starts the shared cooldown; a reply without data pauses only this lane.
"""

from __future__ import annotations

import logging
import random
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import fetch as fetch_mod
from . import scans as scans_mod
from .config import Config, Route
from .deals import REGIONS
from .gflights import PageKind, classify
from .gflights import explore as gx
from .gflights.transport import Transport, TransportError, default_transport
from .source import FailureStore, deep_link, tidy_error
from .store.db import iso, utcnow
from .store.db import now as clock_now
from .watches import list_deal_watches

log = logging.getLogger(__name__)

SOURCE_NAME = "google_explore"
KIND = "explore"
DEFAULT_NIGHTS = 7  # a region watch that names no trip length
BOARD_LIMIT = 60  # places shown on one board

# A deal-watch region → the Explore place asked for it. Where they differ, the reply
# is filtered by the region's words (deals.REGIONS) against name and country.
REGION_PLACES: dict[str, str] = {
    "europe": "europe",
    "asia": "asia",
    "africa": "africa",
    "south america": "south america",
    "oceania": "oceania",
    "japan": "asia",
    "hawaii": "anywhere",
    "mexico": "north america",
    "caribbean": "north america",
}
PLACE_TITLES = {p: p.title() for p in gx.PLACES}
PLACE_TITLES["anywhere"] = "Anywhere"


def departure_weekday(nights: int | None) -> int:
    """Friday for a weekend or one way, Saturday for a week or longer (Mon=0)."""
    return 4 if nights is None or nights < 7 else 5


def nights_label(nights: int | None) -> str:
    if nights is None:
        return "one way"
    if nights <= 3:
        return f"{nights}-night weekends" if nights != 3 else "long weekends"
    if nights == 7:
        return "a week"
    if nights == 14:
        return "two weeks"
    return f"{nights} nights"


@dataclass(frozen=True)
class Board:
    key: str  # 'anywhere-3' or 'watch-12'
    title: str
    origin: str
    region: str  # Explore place name, a key of gflights.explore.PLACES
    nights: int | None  # None = one way
    months: tuple[int, ...] = ()  # () = any month
    words: tuple[str, ...] = ()  # only places whose name or country has one of these
    watch_id: int | None = None
    max_price: int | None = None  # cents, from the watch

    def keeps(self, name: str, country: str | None) -> bool:
        if not self.words:
            return True
        text = f"{name} {country or ''}".lower()
        return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in self.words)


@dataclass(frozen=True)
class ExploreJob:
    origin: str
    region: str
    nights: int | None
    depart: str

    def key(self) -> str:
        n = "ow" if self.nights is None else str(self.nights)
        return f"{self.origin}|{self.region}|{n}|{self.depart}"

    def spec(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "region": self.region,
            "nights": self.nights,
            "depart": self.depart,
        }

    def describe(self) -> str:
        return f"{self.origin}→{self.region} {self.depart} ({nights_label(self.nights)})"


def _night_value(n: int | None) -> int | None:
    """0 nights (one way) is stored and sent as None."""
    return None if n == 0 else n


def boards(conn: sqlite3.Connection, cfg: Config) -> list[Board]:
    """Every board to price: the built-in ones, then one per region watch Explore can ask."""
    out = [
        Board(
            key=f"anywhere-{n}",
            title=f"Anywhere from {cfg.home} · {nights_label(_night_value(n))}",
            origin=cfg.home,
            region="anywhere",
            nights=_night_value(n),
        )
        for n in cfg.explore.boards
    ]
    for w in list_deal_watches(conn, status="active"):
        region = next((d.lower() for d in w.destinations if d.lower() in REGION_PLACES), None)
        if region is None:
            continue  # only free words, e.g. "Lisbon": matched against posts only
        place = REGION_PLACES[region]
        out.append(
            Board(
                key=f"watch-{w.id}",
                title=w.name,
                origin=w.origin or cfg.home,
                region=place,
                nights=_night_value(DEFAULT_NIGHTS if w.nights is None else w.nights),
                months=tuple(w.months),
                words=REGIONS.get(region, ()) if place != region else (),
                watch_id=w.id,
                max_price=w.max_price,
            )
        )
    return out


def board_departures(cfg: Config, board: Board, today: date) -> list[str]:
    """One departure a week within the horizon, on the board's weekday, in its months."""
    first = today + timedelta(days=cfg.min_days_ahead)
    last = today + timedelta(days=cfg.max_days_ahead)
    day = first + timedelta(days=(departure_weekday(board.nights) - first.weekday()) % 7)
    out = []
    while day <= last:
        if not board.months or day.month in board.months:
            out.append(day.isoformat())
        day += timedelta(days=7)
    return out


def plan(conn: sqlite3.Connection, cfg: Config, today: date) -> list[ExploreJob]:
    """Every Explore call the boards need, once each (two boards can share a call)."""
    jobs: dict[str, ExploreJob] = {}
    for b in boards(conn, cfg):
        for d in board_departures(cfg, b, today):
            job = ExploreJob(b.origin, b.region, b.nights, d)
            jobs.setdefault(job.key(), job)
    return sorted(jobs.values(), key=lambda j: j.depart)


def due(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> list[ExploreJob]:
    """The calls to make next: never run, then oldest, the nearest departures first."""
    now = now or clock_now()
    jobs = plan(conn, cfg, now.date())
    scans_mod.mark_active(conn, KIND, [j.key() for j in jobs])
    return scans_mod.stalest(
        jobs,
        ExploreJob.key,
        scans_mod.last_runs(conn, KIND),
        now,
        cfg.explore.refresh_hours,
        tiebreak=lambda j: j.depart,
    )


# ---------------------------------------------------------------------------
# Running calls
# ---------------------------------------------------------------------------


@dataclass
class ExploreStats:
    run_id: int | None = None
    calls: int = 0
    ok: int = 0
    failed: int = 0
    blocked: bool = False
    gated: bool = False
    places: int = 0
    priced: int = 0
    written: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = (
            f"calls={self.calls} ok={self.ok} failed={self.failed} places={self.places} "
            f"priced={self.priced} written={self.written}"
        )
        if self.blocked:
            s += " ABORTED=blocked"
        if self.gated:
            s += " STOPPED=gated"
        return s


def fetch_board(
    transport: Transport, cfg: Config, job: ExploreJob, timeout: float = 30.0
) -> tuple[gx.ExploreResult, Any]:
    body = gx.build_body(
        job.origin,
        job.region,
        job.depart,
        job.nights,
        seat=cfg.search.seat,
        adults=cfg.search.adults,
        carry_on_bags=cfg.search.carry_on_bags,
    )
    params = {**gx.RPC_PARAMS, "curr": cfg.search.currency}
    resp = transport.post(gx.RPC_URL, body, params=params, headers=gx.RPC_HEADERS, timeout=timeout)
    return gx.parse_explore(resp.text), resp


def store(
    conn: sqlite3.Connection,
    cfg: Config,
    job: ExploreJob,
    result: gx.ExploreResult,
    now: datetime,
    dedupe_hours: float = 20.0,
) -> int:
    """Append each priced place's fare, unless the same fare was written lately."""
    stamp, since = iso(now), iso(now - timedelta(hours=dedupe_hours))
    written = 0
    for d in result.priced:
        cents = round(float(d.price or 0) * 100)
        last = conn.execute(
            "SELECT price_cents FROM explore_prices WHERE origin = ? AND place_id = ? "
            "AND nights IS ? AND depart_date = ? AND observed_at >= ? "
            "ORDER BY observed_at DESC, id DESC LIMIT 1",
            (job.origin, d.place_id, job.nights, job.depart, since),
        ).fetchone()
        if last is not None and last[0] == cents:
            continue
        conn.execute(
            """INSERT INTO explore_prices (origin, region, place_id, name, country, airport,
                   depart_date, nights, price_cents, currency, airline, stops, duration_min,
                   observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.origin,
                job.region,
                d.place_id,
                d.name,
                d.country,
                d.airport,
                job.depart,
                job.nights,
                cents,
                cfg.search.currency,
                d.airline_name or d.airline,
                d.stops,
                d.duration_min,
                stamp,
            ),
        )
        written += 1
    return written


def run(
    conn: sqlite3.Connection,
    cfg: Config,
    jobs: list[ExploreJob],
    transport: Transport | None = None,
    now: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> ExploreStats:
    """Make these Explore calls, paced like the others, recording each."""
    stats = ExploreStats()
    now = now or clock_now()
    transport = transport or default_transport(cfg.fetch.transport)
    failures = FailureStore(cfg.failure_dir, keep=cfg.fetch.keep_failure_artifacts)
    cur = conn.execute("INSERT INTO run_log (started_at, kind) VALUES (?, 'explore')", (utcnow(),))
    stats.run_id = cur.lastrowid
    conn.commit()
    notes: list[str] = []

    def attempt(outcome: str, latency: int, error: str | None = None) -> None:
        fetch_mod.log_attempt(conn, stats.run_id, None, SOURCE_NAME, outcome, latency, error)

    for i, job in enumerate(jobs):
        if i:
            sleeper(random.uniform(cfg.fetch.min_sleep_seconds, cfg.fetch.max_sleep_seconds))
        stats.calls += 1
        t0 = time.monotonic()
        try:
            result, resp = fetch_board(transport, cfg, job, cfg.fetch.request_timeout_seconds)
        except TransportError as exc:
            stats.failed += 1
            stats.errors.append(f"{job.describe()}: network: {exc}")
            attempt("network", int((time.monotonic() - t0) * 1000), tidy_error(str(exc)))
            scans_mod.record(conn, KIND, job.key(), job.spec(), "network", now)
            conn.commit()
            continue
        latency = int((time.monotonic() - t0) * 1000)
        page = classify(resp)
        if page.kind is PageKind.BLOCKED:
            stats.failed += 1
            stats.blocked = True
            failures.save("blocked", resp, f"explore-{job.region}")
            attempt("blocked", latency, page.detail)
            scans_mod.record(conn, KIND, job.key(), job.spec(), "blocked", now)
            until = fetch_mod.enter_cooldown(conn, cfg, now)
            notes.append(f"aborted: blocked on Explore; cooldown until {until}")
            break
        if not result.destinations:
            stats.failed += 1
            gated = resp.status < 400 and gx.looks_gated(resp.text)
            outcome = "gated" if gated else "layout"
            msg = f"HTTP {resp.status}, {resp.size} bytes, no destinations"
            failures.save(f"explore-{outcome}", resp, job.region)
            attempt(outcome, latency, msg)
            scans_mod.record(conn, KIND, job.key(), job.spec(), outcome, now)
            stats.errors.append(f"{job.describe()}: {outcome}: {msg}")
            conn.commit()
            if gated:
                stats.gated = True
                notes.append(f"stopped: {msg} (Google declined)")
                break
            continue
        stats.ok += 1
        stats.places += len(result.destinations)
        stats.priced += len(result.priced)
        attempt("ok", latency)
        scans_mod.record(conn, KIND, job.key(), job.spec(), "ok", now)
        stats.written += store(conn, cfg, job, result, now)
        conn.commit()
        log.info(
            "[ok] %s: %d places, %d with a fare",
            job.describe(),
            len(result.destinations),
            len(result.priced),
        )
    conn.execute(
        "UPDATE run_log SET finished_at = ?, attempted = ?, succeeded = ?, failed = ?, "
        "blocked = ?, notes = ? WHERE id = ?",
        (
            utcnow(),
            stats.calls,
            stats.ok,
            stats.failed,
            1 if stats.blocked else 0,
            "; ".join(notes) or None,
            stats.run_id,
        ),
    )
    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Reading boards back
# ---------------------------------------------------------------------------


@dataclass
class BoardPlace:
    place_id: str
    name: str
    country: str | None
    airport: str | None
    price_cents: int
    currency: str
    depart_date: str
    return_date: str | None
    airline: str | None
    stops: int | None
    duration_min: int | None
    observed_at: str
    link: str | None
    under_max: bool


@dataclass
class BoardView:
    board: Board
    departures: int  # departure dates the board prices
    priced_departures: int  # of those, with a fresh fare for some place
    updated_at: str | None
    places: list[BoardPlace]


def view(conn: sqlite3.Connection, cfg: Config, board: Board, now: datetime) -> BoardView:
    """Each place's cheapest current fare over the board's departures. A departure's fare
    is its newest row, and only if seen within `explore.fresh_hours`."""
    today = now.date()
    departures = board_departures(cfg, board, today)
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY place_id, depart_date ORDER BY observed_at DESC, id DESC) AS rn
            FROM explore_prices
            WHERE origin = ? AND region = ? AND nights IS ? AND depart_date >= ?
        )
        SELECT * FROM latest WHERE rn = 1 AND observed_at >= ?
        """,
        (
            board.origin,
            board.region,
            board.nights,
            today.isoformat(),
            iso(now - timedelta(hours=cfg.explore.fresh_hours)),
        ),
    ).fetchall()
    wanted = set(departures)
    best: dict[str, sqlite3.Row] = {}
    seen_dates: set[str] = set()
    for r in rows:
        if r["depart_date"] not in wanted or not board.keeps(r["name"], r["country"]):
            continue
        seen_dates.add(r["depart_date"])
        b = best.get(r["place_id"])
        if b is None or (r["price_cents"], r["depart_date"]) < (b["price_cents"], b["depart_date"]):
            best[r["place_id"]] = r
    places = []
    for r in sorted(best.values(), key=lambda r: (r["price_cents"], r["name"]))[:BOARD_LIMIT]:
        ret = (
            (date.fromisoformat(r["depart_date"]) + timedelta(days=board.nights)).isoformat()
            if board.nights
            else None
        )
        link = None
        if r["airport"]:
            route = Route(r["airport"], r["name"], None, 2, None, origin=board.origin)
            link = deep_link(cfg, route, r["depart_date"], ret)
        places.append(
            BoardPlace(
                place_id=r["place_id"],
                name=r["name"],
                country=r["country"],
                airport=r["airport"],
                price_cents=r["price_cents"],
                currency=r["currency"],
                depart_date=r["depart_date"],
                return_date=ret,
                airline=r["airline"],
                stops=r["stops"],
                duration_min=r["duration_min"],
                observed_at=r["observed_at"],
                link=link,
                under_max=board.max_price is not None and r["price_cents"] <= board.max_price,
            )
        )
    updated = max((r["observed_at"] for r in best.values()), default=None)
    return BoardView(board, len(departures), len(seen_dates), updated, places)
