"""Public, read-only endpoints. Every one opens its own read-only connection.

They are built on the queries the command line and the old page already use:
`report.grid` and `report.advise` for prices and verdicts, `health.check` for
problems, and the run, request and feed SQL from the old status page.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from .. import __version__, airports, pace, report, watches
from .. import fetch as fetch_mod
from .. import health as health_mod
from ..canary import STATE_LAST_CANARY
from ..config import Config
from ..notifier import recent as recent_notifications
from ..scheduler import STATE_LAST_SWEEP, STATE_NEXT_BATCH
from ..settings import Settings
from ..store.backup import list_backups
from ..store.db import connect, get_state, iso, parse_ts
from ..store.db import now as clock_now
from .models import (
    AirportOut,
    AlertOut,
    BackupOut,
    BudgetOut,
    CanaryOut,
    DatePairOut,
    DealOut,
    DestinationOut,
    FeedOut,
    HealthOut,
    HealthSummary,
    HistoryOut,
    LaneOut,
    LayoverOut,
    NotificationOut,
    OfferOut,
    OverviewOut,
    PaceOut,
    PricePoint,
    ProblemOut,
    RequestDay,
    RunOut,
    SearchOut,
    SearchSummary,
    SegmentOut,
    StatusOut,
    TripOut,
    TripSummary,
    VerdictOut,
)
from .ratelimit import RateLimiter

CACHE = "public, max-age=60"  # the worker stores new prices every half hour or so
NO_STORE = "no-store"
SERIES_POINTS = 30  # a trip card's small chart
HISTORY_POINTS = 2000  # a full series (two a day for over two years)
LOW_DAYS = 30
STATUS_DAYS = 14
STATUS_RUNS = 10


def db(request: Request) -> Iterator[sqlite3.Connection]:
    settings: Settings = request.app.state.settings
    if not settings.database.exists():
        raise HTTPException(503, "no database yet; the worker creates it when it starts")
    conn = connect(settings.database, readonly=True)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(db)]


def _cfg(request: Request) -> Config:
    cfg: Config = request.app.state.cfg
    return cfg


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------


def _age_hours(at: str | None, now: datetime) -> float | None:
    dt = parse_ts(at)
    return None if dt is None else round((now - dt).total_seconds() / 3600.0, 1)


def series(
    conn: sqlite3.Connection, query_id: int, limit: int, source: str | None = None
) -> list[tuple[str, int]]:
    """The newest `limit` points of a date pair's price series, oldest first. Without
    `source`, the canonical series (each fetch's cheapest offer); with
    `source='google_calendar'`, the calendar sweep's estimates."""
    where = "is_best = 1" if source is None else "source = ?"
    params: list[object] = [query_id] if source is None else [query_id, source]
    params.append(limit)
    rows = conn.execute(
        f"SELECT observed_at, price_cents FROM observations WHERE query_id = ? AND {where} "
        "ORDER BY observed_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return [(r[0], int(r[1])) for r in reversed(rows)]


def _points(pts: list[tuple[str, int]]) -> list[PricePoint]:
    return [PricePoint(at=at, price=p) for at, p in pts]


def change_7d(pts: list[tuple[str, int]], now: datetime) -> float | None:
    """The newest price against the last one at least a week old, in percent."""
    if not pts:
        return None
    week_ago = iso(now - timedelta(days=7))
    older = [p for at, p in pts if at <= week_ago]
    if not older or not older[-1]:
        return None
    return round((pts[-1][1] - older[-1]) / older[-1] * 100.0, 1)


def _label(conn: sqlite3.Connection, origin: str, destination: str) -> str:
    row = conn.execute(
        "SELECT label FROM routes WHERE origin = ? AND destination = ?", (origin, destination)
    ).fetchone()
    return str(row["label"]) if row else airports.city(destination)


def trip_query(conn: sqlite3.Connection, trip: watches.Trip) -> sqlite3.Row | None:
    """The date pair a trip tracks. Looked up by route and dates rather than through
    `watch_queries`, so a paused or booked trip still shows its history."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT q.id, q.deep_link, q.active FROM queries q JOIN routes r ON r.id = q.route_id "
        "WHERE r.origin = ? AND r.destination = ? AND q.depart_date = ? "
        "AND q.return_date IS ? ORDER BY q.active DESC, q.id LIMIT 1",
        (trip.origin, trip.destination, trip.depart_date, trip.return_date),
    ).fetchone()
    return row


def _advice(a: report.Advice) -> VerdictOut:
    return VerdictOut(
        verdict=a.verdict,
        reasons=a.reasons,
        days_to_departure=a.days_to_departure,
        observations=a.n_obs,
        all_time_low=a.all_time_low,
        route_p20=a.route_p20,
        route_p50=a.route_p50,
        change_7d_pct=None if a.trend_7d_pct is None else round(a.trend_7d_pct, 1),
    )


def _trip(
    conn: sqlite3.Connection,
    cfg: Config,
    trip: watches.Trip,
    now: datetime,
    points: int,
) -> tuple[TripSummary, int | None, VerdictOut | None]:
    q = trip_query(conn, trip)
    qid = int(q["id"]) if q else None
    pts = series(conn, qid, HISTORY_POINTS) if qid else []
    cutoff = iso(now - timedelta(days=LOW_DAYS))
    recent = [p for at, p in pts if at >= cutoff]
    advice = None
    if qid is not None and trip.status == "active" and pts:
        items = report.advise(
            conn,
            query_ids=[qid],
            window_days=LOW_DAYS,
            route_window_days=cfg.alerts.percentile_window_days,
            today=now,
        )
        advice = _advice(items[0]) if items else None
    target = watches.target_for(conn, qid) if qid else None
    summary = TripSummary(
        slug=trip.slug,
        name=trip.name,
        origin=trip.origin,
        destination=trip.destination,
        label=_label(conn, trip.origin, trip.destination),
        depart_date=trip.depart_date,
        return_date=trip.return_date,
        status=trip.status,
        target_price=trip.target_price if trip.target_price is not None else target,
        max_stops=trip.max_stops,
        price=pts[-1][1] if pts else None,
        price_at=pts[-1][0] if pts else None,
        change_7d_pct=change_7d(pts, now),
        verdict=advice.verdict if advice else None,
        all_time_low=min(p for _, p in pts) if pts else None,
        low_30d=min(recent) if recent else None,
        observations=len(pts),
        days_to_departure=(date.fromisoformat(trip.depart_date) - now.date()).days,
        series=_points(pts[-points:]),
        link=q["deep_link"] if q else None,
    )
    return summary, qid, advice


def _pair(r: report.GridRow, origin: str, now: datetime) -> DatePairOut:
    if r.current_cents is not None:
        price, source, at = r.current_cents, "detail", r.current_at
    elif r.sweep_cents is not None:
        price, source, at = r.sweep_cents, "calendar", r.sweep_at
    else:
        price, source, at = None, None, None
    flags = []
    if price is not None and r.target_price is not None and price <= r.target_price:
        flags.append("under_target")
    if source == "detail" and r.is_at_low:
        flags.append("at_low")
    if source == "calendar":
        flags.append("estimate")
    return DatePairOut(
        query_id=r.query_id,
        origin=origin,
        destination=r.dest,
        depart_date=r.depart_date,
        return_date=r.return_date,
        price=price,
        source=source,
        price_at=at,
        age_hours=_age_hours(at, now),
        low_30d=r.low_cents,
        calendar_price=r.sweep_cents,
        calendar_at=r.sweep_at,
        airline=r.airline,
        stops=r.stops,
        duration_min=r.duration_min,
        target_price=r.target_price,
        flags=flags,
        link=r.deep_link,
    )


def _best(pairs: list[DatePairOut]) -> DatePairOut | None:
    """The cheapest date: a confirmed (detail) price when there is one, since calendar
    prices are estimates that include basic economy."""
    detail = [p for p in pairs if p.source == "detail" and p.price is not None]
    pool = detail or [p for p in pairs if p.price is not None]
    return min(pool, key=lambda p: (p.price or 0, p.depart_date)) if pool else None


def _search_pairs(
    conn: sqlite3.Connection, search: watches.Search, now: datetime
) -> list[DatePairOut]:
    ids = [
        int(r[0])
        for r in conn.execute(
            "SELECT query_id FROM watch_queries WHERE search_id = ?", (search.id,)
        )
    ]
    rows = report.grid(conn, window_days=LOW_DAYS, include_unpriced=True, query_ids=ids)
    pairs = [_pair(r, search.origin, now) for r in rows]
    for dest in {p.destination for p in pairs}:
        best = _best([p for p in pairs if p.destination == dest])
        if best is not None:
            best.flags.insert(0, "cheapest")
    return pairs


def _search_summary(
    conn: sqlite3.Connection, search: watches.Search, now: datetime
) -> tuple[SearchSummary, list[DatePairOut]]:
    pairs = _search_pairs(conn, search, now)
    summary = SearchSummary(
        slug=search.slug,
        name=search.name,
        origin=search.origin,
        destinations=search.destinations,
        depart_dows=search.depart_dows,
        nights=search.nights,
        status=search.status,
        target_price=search.target_price,
        pairs=len(pairs),
        priced=sum(1 for p in pairs if p.price is not None),
        best=_best(pairs),
    )
    return summary, pairs


def _problems(rep: health_mod.HealthReport) -> list[ProblemOut]:
    return [ProblemOut(code=p.code, severity=p.severity, message=p.message) for p in rep.problems]


_LANE_SOURCES = {
    "calendar": "source = 'google_calendar'",
    "explore": "source = 'google_explore'",
    "detail": "source NOT IN ('google_calendar', 'google_explore', 'canary', 'serpapi')",
}


def _lanes(conn: sqlite3.Connection, cfg: Config, now: datetime) -> list[LaneOut]:
    """Each lane's share, use in the last 24 hours, pause and last answer."""
    shares = pace.lane_shares(cfg)
    used = pace.lane_used(conn, now)
    out = []
    for name in pace.LANES:
        last = conn.execute(
            f"SELECT outcome, at FROM fetch_attempts WHERE {_LANE_SOURCES[name]} "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        rec = pace.lane_pause(conn, name) or {}
        out.append(
            LaneOut(
                name=name,
                enabled=name in shares,
                share=round(shares.get(name, 0.0), 3),
                used=used[name],
                paused_until=pace.lane_paused(conn, name, now),
                pause_reason=rec.get("reason"),
                last_outcome=last["outcome"] if last else None,
                last_at=last["at"] if last else None,
            )
        )
    return out


def _calendar_windows(conn: sqlite3.Connection, cfg: Config, now: datetime) -> tuple[int, int]:
    """Calendar windows in use, and how many are older than `calendar.refresh_hours`."""
    cut = iso(now - timedelta(hours=cfg.calendar.refresh_hours))
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(last_run_at IS NULL OR last_run_at < ?), 0) FROM scans "
        "WHERE kind = 'calendar' AND active = 1",
        (cut,),
    ).fetchone()
    return int(row[0]), int(row[1])


def _deals(conn: sqlite3.Connection, limit: int, *, matched_only: bool) -> list[DealOut]:
    where = "WHERE watch IS NOT NULL " if matched_only else ""
    rows = conn.execute(
        "SELECT id, title, link, price_cents, watch, feed, published_at, seen_at FROM deal_posts "
        f"{where}ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        DealOut(
            id=r["id"],
            title=r["title"],
            link=r["link"],
            price=r["price_cents"],
            watch=r["watch"],
            feed=r["feed"],
            published_at=r["published_at"],
            seen_at=r["seen_at"],
        )
        for r in rows
    ]


def _offers(conn: sqlite3.Connection, query_id: int) -> tuple[list[OfferOut], str | None]:
    """Every offer stored by the newest detail fetch of a date pair, cheapest first."""
    newest = conn.execute(
        "SELECT observed_at FROM observations WHERE query_id = ? AND is_best = 1 "
        "ORDER BY observed_at DESC, id DESC LIMIT 1",
        (query_id,),
    ).fetchone()
    if newest is None:
        return [], None
    rows = conn.execute(
        "SELECT price_cents, airline, stops, duration_min, raw, source FROM observations "
        "WHERE query_id = ? AND observed_at = ? AND source IS NOT 'google_calendar' "
        "ORDER BY price_cents, id",
        (query_id, newest[0]),
    ).fetchall()
    out = []
    for r in rows:
        try:
            raw: dict[str, Any] = json.loads(r["raw"] or "{}")
            if not isinstance(raw, dict):
                raw = {}
        except ValueError:
            raw = {}
        segs = [s for s in raw.get("segments") or [] if isinstance(s, dict)]
        lays = [x for x in raw.get("layovers") or [] if isinstance(x, dict)]
        segments = [
            SegmentOut(
                origin=s.get("from"),
                destination=s.get("to"),
                departs=_clean_time(s.get("dep")),
                arrives=_clean_time(s.get("arr")),
                minutes=s.get("min") if isinstance(s.get("min"), int) else None,
                flight=s.get("flight"),
                aircraft=s.get("aircraft"),
            )
            for s in segs
        ]
        out.append(
            OfferOut(
                price=r["price_cents"],
                airline=r["airline"],
                stops=r["stops"],
                duration_min=r["duration_min"],
                departs=segments[0].departs if segments else None,
                arrives=segments[-1].arrives if segments else None,
                layovers=[
                    LayoverOut(
                        minutes=x.get("min") if isinstance(x.get("min"), int) else None,
                        airport=x.get("at") if isinstance(x.get("at"), str) else None,
                    )
                    for x in lays
                ],
                segments=segments,
                source=r["source"],
            )
        )
    return out, newest[0]


def _clean_time(v: object) -> str | None:
    """Segment times are stored as "YYYY-MM-DD HH:MM"; a missing part reads "None"."""
    if not isinstance(v, str) or "None" in v or not v.strip():
        return None
    return v.strip()


def newest_price_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(observed_at) FROM observations WHERE is_best = 1").fetchone()
    return row[0] if row and row[0] else None


# ---------------------------------------------------------------------------
# the router
# ---------------------------------------------------------------------------


def make_router(reads: RateLimiter, airport_search: RateLimiter) -> APIRouter:
    """Public reads, `reads` requests per client (60 a minute in the app); the airport
    search has its own, tighter limit."""

    def limit_reads(request: Request) -> None:
        reads.check(request)

    def limit_airports(request: Request) -> None:
        airport_search.check(request)

    r = APIRouter(prefix="/api")
    limited = [Depends(limit_reads)]

    @r.get("/health", response_model=HealthOut, dependencies=limited)
    def health(request: Request) -> JSONResponse:
        """503 when the newest price is older than `health.max_hours_without_success`,
        or the worker's heartbeat file exists but has gone stale."""
        settings, cfg = _settings(request), _cfg(request)
        now = clock_now()
        max_age_s = cfg.health.max_hours_without_success * 3600
        newest = None
        if settings.database.exists():
            try:
                conn = connect(settings.database, readonly=True)
                try:
                    newest = newest_price_at(conn)
                finally:
                    conn.close()
            except sqlite3.Error:
                newest = None
        beat = health_mod.heartbeat_age(settings.heartbeat_path, now)
        dt = parse_ts(newest)
        age_s = None if dt is None else round((now - dt).total_seconds())
        if dt is None or age_s is None:
            status = "no_data"
        elif age_s > max_age_s:
            status = "stale"
        elif beat is not None and beat > health_mod.HEARTBEAT_MAX_AGE_S:
            status = "worker_stopped"
        else:
            status = "ok"
        body = HealthOut(
            status=status,
            newest_price_at=newest,
            age_s=age_s,
            max_age_s=max_age_s,
            worker_heartbeat_age_s=None if beat is None else round(beat),
        )
        return JSONResponse(
            body.model_dump(),
            status_code=200 if status == "ok" else 503,
            headers={"Cache-Control": NO_STORE},
        )

    @r.get("/overview", response_model=OverviewOut, dependencies=limited)
    def overview(request: Request, conn: Conn, response: Response) -> OverviewOut:
        """Trip summaries, the best date of each search, recent alerts and deals, health."""
        cfg = _cfg(request)
        now = clock_now()
        order = {"active": 0, "booked": 1, "paused": 2}
        trips = sorted(
            (t for t in watches.list_trips(conn) if t.status != "archived"),
            key=lambda t: (order.get(t.status, 3), t.depart_date, t.id),
        )
        searches = [s for s in watches.list_searches(conn) if s.status != "archived"]
        rep = health_mod.check(conn, cfg, now)
        response.headers["Cache-Control"] = CACHE
        return OverviewOut(
            generated_at=iso(now),
            home_airport=cfg.home,
            trips=[_trip(conn, cfg, t, now, SERIES_POINTS)[0] for t in trips],
            searches=[_search_summary(conn, s, now)[0] for s in searches],
            notifications=[
                NotificationOut.model_validate(dict(n)) for n in recent_notifications(conn, 10)
            ],
            deals=_deals(conn, 10, matched_only=True),
            health=HealthSummary(
                ok=rep.ok,
                problems=_problems(rep),
                last_success_at=get_state(conn, fetch_mod.STATE_LAST_SUCCESS),
                cooldown_until=fetch_mod.cooldown_active(conn, now),
            ),
        )

    @r.get("/trips/{slug}", response_model=TripOut, dependencies=limited)
    def trip(slug: str, request: Request, conn: Conn, response: Response) -> TripOut:
        """A trip's whole price series, newest offers, verdict and alert history."""
        cfg = _cfg(request)
        now = clock_now()
        try:
            t = watches.get_trip(conn, slug)
        except watches.NotFound:
            raise HTTPException(404, f"no trip {slug!r}") from None
        summary, qid, advice = _trip(conn, cfg, t, now, HISTORY_POINTS)
        offers, offers_at = _offers(conn, qid) if qid else ([], None)
        alerts = (
            [
                AlertOut(sent_at=a["sent_at"], reason=a["reason"], price=a["price_cents"])
                for a in conn.execute(
                    "SELECT sent_at, reason, price_cents FROM alerts_sent WHERE query_id = ? "
                    "ORDER BY sent_at DESC, id DESC LIMIT 50",
                    (qid,),
                )
            ]
            if qid
            else []
        )
        response.headers["Cache-Control"] = CACHE
        return TripOut(
            **summary.model_dump(),
            id=t.id,
            notes=t.notes,
            query_id=qid,
            offers=offers,
            offers_at=offers_at,
            advice=advice,
            alerts=alerts,
        )

    @r.get("/searches/{slug}", response_model=SearchOut, dependencies=limited)
    def search(slug: str, conn: Conn, response: Response) -> SearchOut:
        """Every date pair of a flexible search, by destination, with its price and source."""
        now = clock_now()
        try:
            s = watches.get_search(conn, slug)
        except watches.NotFound:
            raise HTTPException(404, f"no search {slug!r}") from None
        summary, pairs = _search_summary(conn, s, now)
        grid = [
            DestinationOut(
                code=d,
                label=_label(conn, s.origin, d),
                pairs=[p for p in pairs if p.destination == d],
            )
            for d in s.destinations
        ]
        response.headers["Cache-Control"] = CACHE
        return SearchOut(
            **summary.model_dump(),
            id=s.id,
            window_start=s.window_start,
            window_end=s.window_end,
            min_days_ahead=s.min_days_ahead,
            max_stops=s.max_stops,
            priority=s.priority,
            grid=grid,
        )

    @r.get("/queries/{query_id}/history", response_model=HistoryOut, dependencies=limited)
    def history(query_id: int, conn: Conn, response: Response) -> HistoryOut:
        """The price series of one date pair, and the calendar's estimates for it."""
        q = conn.execute(
            "SELECT q.id, q.depart_date, q.return_date, q.deep_link, q.active, r.origin, "
            "r.destination, r.active AS route_active FROM queries q "
            "JOIN routes r ON r.id = q.route_id WHERE q.id = ?",
            (query_id,),
        ).fetchone()
        if q is None:
            raise HTTPException(404, f"no date pair {query_id}")
        response.headers["Cache-Control"] = CACHE
        return HistoryOut(
            query_id=q["id"],
            origin=q["origin"],
            destination=q["destination"],
            depart_date=q["depart_date"],
            return_date=q["return_date"],
            active=bool(q["active"] and q["route_active"]),
            target_price=watches.target_for(conn, query_id),
            points=_points(series(conn, query_id, HISTORY_POINTS)),
            calendar=_points(series(conn, query_id, HISTORY_POINTS, source="google_calendar")),
            link=q["deep_link"],
        )

    @r.get("/deals", response_model=list[DealOut], dependencies=limited)
    def deals(
        conn: Conn,
        response: Response,
        limit: int = Query(50, ge=1, le=200),
        matched: bool = Query(False, description="only posts that matched something"),
    ) -> list[DealOut]:
        """Posts from the deal pages and feeds, newest first. `watch` names the deal
        watch, trip or flexible search a post matched (and alerted for), else null."""
        response.headers["Cache-Control"] = CACHE
        return _deals(conn, limit, matched_only=matched)

    @r.get("/status", response_model=StatusOut, dependencies=limited)
    def status(request: Request, conn: Conn, response: Response) -> StatusOut:
        """Is the collector working: problems, runs, requests, budget, feeds, backups."""
        settings, cfg = _settings(request), _cfg(request)
        now = clock_now()
        rep = health_mod.check(conn, cfg, now)
        since = iso(now - timedelta(days=STATUS_DAYS))
        by_day: dict[str, dict[str, int]] = {}
        for row in conn.execute(
            "SELECT substr(at, 1, 10) AS d, outcome, COUNT(*) AS n FROM fetch_attempts "
            "WHERE at >= ? GROUP BY d, outcome ORDER BY d",
            (since,),
        ):
            by_day.setdefault(row["d"], {})[row["outcome"]] = row["n"]
        runs = [
            RunOut(
                id=r["id"],
                kind=r["kind"],
                started_at=r["started_at"],
                finished_at=r["finished_at"],
                attempted=r["attempted"],
                succeeded=r["succeeded"],
                failed=r["failed"],
                blocked=bool(r["blocked"]),
                skipped=bool(r["skipped"]),
                notes=r["notes"],
            )
            for r in conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (STATUS_RUNS,))
        ]
        names = {f.url: f.name for f in watches.list_deal_feeds(conn)}
        feeds = []
        for url in watches.active_feed_urls(conn, cfg) if cfg.deals.enabled else []:
            last = conn.execute(
                "SELECT at, ok, items, error FROM feed_log WHERE feed = ? ORDER BY id DESC LIMIT 1",
                (url,),
            ).fetchone()
            last_ok = conn.execute(
                "SELECT MAX(at) FROM feed_log WHERE feed = ? AND ok = 1", (url,)
            ).fetchone()[0]
            feeds.append(
                FeedOut(
                    url=url,
                    name=names.get(url),
                    last_at=last["at"] if last else None,
                    last_ok_at=last_ok,
                    last_items=last["items"] if last else None,
                    last_error=last["error"] if last else None,
                )
            )
        canary = None
        raw = get_state(conn, STATE_LAST_CANARY)
        if raw:
            try:
                c = json.loads(raw)
                detail = c.get("detail")
                canary = CanaryOut(
                    at=c.get("at"),
                    result=c.get("result"),
                    detail=detail if isinstance(detail, str) else json.dumps(detail),
                )
            except (ValueError, AttributeError):
                canary = CanaryOut(at=None, result=None, detail=raw)
        used = fetch_mod.budget_used(conn, now)
        p = pace.load(conn, cfg)
        limit = p.per_day
        windows, windows_due = _calendar_windows(conn, cfg, now)
        beat = health_mod.heartbeat_age(settings.heartbeat_path, now)
        try:
            db_bytes = settings.database.stat().st_size
        except OSError:
            db_bytes = 0

        def count(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])

        response.headers["Cache-Control"] = NO_STORE
        return StatusOut(
            generated_at=iso(now),
            version=__version__,
            commit=settings.git_commit or None,
            ok=rep.ok,
            checked_at=rep.checked_at,
            problems=_problems(rep),
            info=rep.info,
            last_success_at=get_state(conn, fetch_mod.STATE_LAST_SUCCESS),
            newest_price_at=newest_price_at(conn),
            cooldown_until=fetch_mod.cooldown_active(conn, now),
            blocked_streak=int(get_state(conn, fetch_mod.STATE_BLOCKED_STREAK, "0") or 0),
            next_batch_at=get_state(conn, STATE_NEXT_BATCH),
            last_sweep_at=get_state(conn, STATE_LAST_SWEEP),
            budget=BudgetOut(used=used, limit=limit, remaining=max(0, limit - used)),
            pace=PaceOut(
                per_day=p.per_day,
                ceiling=p.ceiling,
                fixed=p.fixed,
                reason=p.reason,
                changed_at=p.changed_at,
                hold_until=p.hold_until,
                last_block_at=p.blocks[-1]["at"] if p.blocks else None,
                next_review_at=pace.next_review_at(p),
                gap_seconds=round(pace.gap_seconds(conn, cfg)),
            ),
            lanes=_lanes(conn, cfg, now),
            calendar_windows=windows,
            calendar_windows_due=windows_due,
            requests_by_day=[RequestDay(day=d, outcomes=o) for d, o in sorted(by_day.items())],
            recent_runs=runs,
            feeds=feeds,
            canary=canary,
            backups=[
                BackupOut(
                    name=p.name,
                    bytes=p.stat().st_size,
                    created_at=iso(datetime.fromtimestamp(p.stat().st_mtime, now.tzinfo)),
                )
                for p in reversed(list_backups(settings.backup_dir))
            ],
            database_bytes=db_bytes,
            observations=count("SELECT COUNT(*) FROM observations WHERE is_best = 1"),
            active_pairs=count(
                "SELECT COUNT(*) FROM queries q JOIN routes r ON r.id = q.route_id "
                "WHERE q.active = 1 AND r.active = 1"
            ),
            jobs_queued=count("SELECT COUNT(*) FROM jobs WHERE status = 'queued'"),
            worker_heartbeat_age_s=None if beat is None else round(beat),
        )

    @r.get("/airports", response_model=list[AirportOut], dependencies=[Depends(limit_airports)])
    def airport_list(
        response: Response, q: str = Query(min_length=1, max_length=40)
    ) -> list[AirportOut]:
        """Airports with scheduled flights whose code, name or city matches `q`."""
        response.headers["Cache-Control"] = "public, max-age=86400"
        return [
            AirportOut(iata=a.iata, name=a.name, city=a.city, country=a.country)
            for a in airports.search(q, limit=10)
        ]

    return r
