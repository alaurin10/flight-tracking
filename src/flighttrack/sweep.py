"""Calendar sweep: one RPC per route × pattern window instead of one page per date.

EXPERIMENTAL — see gflights/calendar.py for what is and is not verified.

Flow per run:
    1. plan     every (route, pattern) with a fixed trip length → date windows
    2. sweep    POST the calendar RPC per window, record a price per date
                (source='google_calendar', is_best=0: context, not the series)
    3. confirm  the most promising dates get a real results fetch right now,
                through the normal source chain and pacing, so an alert is
                only ever raised on a price we actually saw on the results page

The sweep shares the blocked-cooldown state with the fetcher: if either is
blocked, both wait.
"""

from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import fetch as fetch_mod
from .config import Config, Pattern, Route
from .db import get_state, set_state, utcnow
from .expand import departure_dates
from .gflights import PageKind, classify
from .gflights.calendar import RPC_HEADERS, RPC_PARAMS, RPC_URL, DatePrice, build_body, parse_calendar, windows
from .gflights.transport import Transport, TransportError, default_transport
from .source import FailureStore, Offer, Source, tidy_error

STATE_CURSOR = "sweep_cursor"
SOURCE_NAME = "google_calendar"


@dataclass(frozen=True)
class SweepJob:
    route: Route
    pattern: Pattern
    start: str
    end: str

    def describe(self) -> str:
        return f"{self.route.dest} {self.pattern.name} {self.start}..{self.end}"


@dataclass
class SweepStats:
    run_id: int | None = None
    planned: int = 0
    calls: int = 0
    ok: int = 0
    failed: int = 0
    skipped: bool = False
    blocked: bool = False
    dates_priced: int = 0
    matched: int = 0
    written: int = 0
    confirm_ids: list[int] = field(default_factory=list)
    confirm_stats: fetch_mod.FetchStats | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.skipped:
            return "SKIPPED (cooldown)"
        s = (f"planned={self.planned} calls={self.calls} ok={self.ok} failed={self.failed} "
             f"dates={self.dates_priced} matched={self.matched} written={self.written} "
             f"confirm={len(self.confirm_ids)}")
        if self.blocked:
            s += " ABORTED=blocked"
        return s


def plan(cfg: Config, today: date | None = None) -> list[SweepJob]:
    """Every (route, pattern) window, chunked to what the endpoint fills."""
    today = today or date.today()
    jobs: list[SweepJob] = []
    for route in sorted(cfg.routes, key=lambda r: r.priority):
        for pattern in cfg.patterns:
            if not pattern.applies_to(route.dest):
                continue
            dates = departure_dates(cfg, pattern, today)
            if not dates:
                continue
            for start, end in windows(dates[0], dates[-1]):
                jobs.append(SweepJob(route, pattern, start, end))
    return jobs


def _rotate(jobs: list[SweepJob], conn: sqlite3.Connection, cap: int) -> list[SweepJob]:
    """Take `cap` jobs per run, continuing where the last run stopped."""
    if len(jobs) <= cap:
        return jobs
    cursor = int(get_state(conn, STATE_CURSOR, "0") or 0) % len(jobs)
    picked = [jobs[(cursor + i) % len(jobs)] for i in range(cap)]
    set_state(conn, STATE_CURSOR, str((cursor + cap) % len(jobs)))
    return picked


def _query_lookup(conn: sqlite3.Connection, cfg: Config, route: Route) -> dict[tuple[str, str | None], int]:
    rows = conn.execute(
        """
        SELECT q.id, q.depart_date, q.return_date FROM queries q
        JOIN routes r ON r.id = q.route_id
        WHERE r.origin = ? AND r.destination = ? AND q.active = 1
        """,
        (cfg.home, route.dest),
    ).fetchall()
    return {(r["depart_date"], r["return_date"]): r["id"] for r in rows}


def fetch_window(transport: Transport, cfg: Config, job: SweepJob, timeout: float = 30.0):
    """One RPC. Returns (list[DatePrice], response). Raises TransportError."""
    nights = job.pattern.nights or None
    body = build_body(
        cfg.home, job.route.dest, job.start, job.end, nights,
        seat=cfg.search.seat, adults=cfg.search.adults, max_stops=job.route.max_stops,
        currency=cfg.search.currency,
    )
    params = {**RPC_PARAMS, "hl": "en", "curr": cfg.search.currency}
    resp = transport.post(RPC_URL, body, params=params, headers=RPC_HEADERS, timeout=timeout)
    return parse_calendar(resp.text), resp


def promising(conn: sqlite3.Connection, cfg: Config, query_id: int, sweep_cents: int, target: int | None) -> tuple[bool, str]:
    """Is this sweep price worth a confirming fetch? Returns (yes, why)."""
    if target and sweep_cents <= target:
        return True, "under target"
    row = conn.execute(
        """
        SELECT MIN(price_cents) AS lo FROM observations
        WHERE query_id = ? AND is_best = 1 AND observed_at >= ?
        """,
        (query_id, (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    ).fetchone()
    lo = row["lo"] if row else None
    if lo is None:
        return False, "no detail history yet"
    if sweep_cents <= lo * (1 - cfg.calendar.priority_boost_pct / 100.0):
        return True, f"{(lo - sweep_cents) / lo * 100:.0f}% under 30d low"
    return False, "not notable"


def run(
    conn: sqlite3.Connection,
    cfg: Config,
    transport: Transport | None = None,
    confirm_source: Source | None = None,
    now: datetime | None = None,
    sleeper=time.sleep,
    verbose: bool = True,
) -> SweepStats:
    stats = SweepStats()
    now = now or datetime.now(timezone.utc)
    transport = transport or default_transport(cfg.fetch.transport)
    failures = FailureStore(cfg.failure_dir, keep=cfg.fetch.keep_failure_artifacts)

    cur = conn.execute("INSERT INTO run_log (started_at, kind) VALUES (?, 'sweep')", (utcnow(),))
    stats.run_id = cur.lastrowid
    conn.commit()

    until = fetch_mod.cooldown_active(conn, now)
    if until:
        stats.skipped = True
        conn.execute("UPDATE run_log SET finished_at = ?, skipped = 1, notes = ? WHERE id = ?",
                     (utcnow(), f"skipped: in cooldown until {until}", stats.run_id))
        conn.commit()
        if verbose:
            print(f"[sweep] skipped — cooldown until {until}")
        return stats

    jobs = plan(cfg, now.date())
    stats.planned = len(jobs)
    jobs = _rotate(jobs, conn, cfg.calendar.max_calls_per_run)
    if verbose:
        print(f"[sweep] run {stats.run_id}: {len(jobs)} of {stats.planned} windows this run")

    candidates: list[tuple[float, int, str]] = []   # (sweep/target ratio, query_id, why)
    notes: list[str] = []

    for i, job in enumerate(jobs):
        if i:
            sleeper(random.uniform(cfg.fetch.min_sleep_seconds, cfg.fetch.max_sleep_seconds))
        stats.calls += 1
        t0 = time.monotonic()
        try:
            prices, resp = fetch_window(transport, cfg, job, timeout=cfg.fetch.request_timeout_seconds)
        except TransportError as exc:
            stats.failed += 1
            stats.errors.append(f"{job.describe()}: network: {exc}")
            fetch_mod.log_attempt(conn, stats.run_id, None, SOURCE_NAME, "network", int((time.monotonic() - t0) * 1000), tidy_error(str(exc)))
            conn.commit()
            if verbose:
                print(f"  [FAIL:network] {job.describe()}: {exc}")
            continue
        latency = int((time.monotonic() - t0) * 1000)

        page = classify(resp)
        if page.kind is PageKind.BLOCKED:
            stats.failed += 1
            stats.blocked = True
            failures.save("blocked", resp, "calendar-" + job.route.dest)
            fetch_mod.log_attempt(conn, stats.run_id, None, SOURCE_NAME, "blocked", latency, page.detail)
            until = fetch_mod.enter_cooldown(conn, cfg, now)
            notes.append(f"aborted: blocked on calendar RPC; cooldown until {until}")
            if verbose:
                print(f"  [ABORT] blocked — cooldown until {until}")
            break
        if not prices:
            stats.failed += 1
            path = failures.save("calendar-shape", resp, job.route.dest)
            msg = f"HTTP {resp.status}, {resp.size} bytes, no (date, price) tuples found"
            stats.errors.append(f"{job.describe()}: layout: {msg}")
            fetch_mod.log_attempt(conn, stats.run_id, None, SOURCE_NAME, "layout", latency, msg)
            conn.commit()
            if verbose:
                print(f"  [FAIL:layout] {job.describe()}: {msg}" + (f" — saved {path}" if path else ""))
            continue

        stats.ok += 1
        stats.dates_priced += len(prices)
        fetch_mod.log_attempt(conn, stats.run_id, None, SOURCE_NAME, "ok", latency)
        lookup = _query_lookup(conn, cfg, job.route)
        nights = job.pattern.nights
        hit = 0
        for dp in prices:
            ret = dp.ret or ((date.fromisoformat(dp.depart) + timedelta(days=nights)).isoformat() if nights else None)
            qid = lookup.get((dp.depart, ret))
            if qid is None:
                continue
            hit += 1
            cents = int(round(float(dp.price) * 100))
            offer = Offer(price_cents=cents, currency=cfg.search.currency, source=SOURCE_NAME,
                          raw={"price_raw": dp.price, "calendar": True, "window": [job.start, job.end]})
            w, _ = fetch_mod.store(conn, qid, [offer], is_best_series=False)
            stats.written += w
            ok, why = promising(conn, cfg, qid, cents, job.route.target_price)
            if ok:
                ratio = cents / (job.route.target_price or cents)
                candidates.append((ratio, qid, why))
        stats.matched += hit
        conn.commit()
        if verbose:
            lo = min(prices, key=lambda d: d.price)
            print(f"  [ok] {job.describe()}: {len(prices)} dates, {hit} matched, lowest {lo.depart} ${lo.price:,.0f}")

    # Confirm the best few with a real results fetch, through the normal chain.
    if candidates and confirm_source is not None and cfg.calendar.confirm_budget > 0 and not stats.blocked:
        candidates.sort()
        seen: set[int] = set()
        for _, qid, _why in candidates:
            if qid not in seen:
                seen.add(qid)
                stats.confirm_ids.append(qid)
            if len(stats.confirm_ids) >= cfg.calendar.confirm_budget:
                break
        if verbose:
            print(f"[sweep] confirming {len(stats.confirm_ids)} candidate(s) with detail fetches")
        stats.confirm_stats = fetch_mod.run(
            conn, cfg, confirm_source, now=now, sleeper=sleeper, verbose=verbose,
            query_ids=stats.confirm_ids, limit=len(stats.confirm_ids), kind="confirm",
        )

    conn.execute(
        "UPDATE run_log SET finished_at = ?, attempted = ?, succeeded = ?, failed = ?, blocked = ?, notes = ? WHERE id = ?",
        (utcnow(), stats.calls, stats.ok, stats.failed, 1 if stats.blocked else 0,
         "; ".join(notes) if notes else None, stats.run_id),
    )
    conn.commit()
    return stats
