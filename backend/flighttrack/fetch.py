"""Read due queries, call the source, append observations.

Knows nothing about alerting. The two must be able to fail independently: a
broken notification channel should never stop history from accumulating, and a
blocked scraper should never silently look like "no deals today".

Pacing is deliberately conservative. The binding constraint on this whole
system is an undocumented per-IP limit at Google, so the rule is: serial,
jittered, capped, and never retry into a failure. What is new here versus the
first version is that the rule is now *enforced by state* rather than by a
warning in the README:

  * a run that aborts on blocking writes a `cooldown_until` into the `state`
    table, escalating with each consecutive blocked run, and the next run
    skips itself while it is in the future;
  * every request is logged to `fetch_attempts` with its outcome and latency,
    so reliability is measured continuously rather than once by `calibrate`;
  * failures are typed (see source.py) and each type gets its own response.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import Config, Route
from .source import (
    BlockedError,
    LayoutError,
    NetworkError,
    NoResults,
    Offer,
    Source,
    SourceError,
    price_is_plausible,
)
from .store.db import get_state, iso, parse_ts, set_state, utcnow
from .store.db import now as clock_now

log = logging.getLogger(__name__)

# How stale a query may get before it is due again, by route priority.
PRIORITY_INTERVAL_DAYS = {1: 1, 2: 3, 3: 7}

STATE_COOLDOWN_UNTIL = "cooldown_until"
STATE_BLOCKED_STREAK = "blocked_streak"
STATE_LAST_SUCCESS = "last_success_at"


@dataclass
class DueQuery:
    query_id: int
    route: Route
    depart_date: str
    return_date: str | None
    pattern: str
    label: str
    last_fetch_at: str | None


@dataclass
class FetchStats:
    run_id: int | None = None
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    empty: int = 0
    retries: int = 0
    blocked: bool = False
    skipped: bool = False
    abort_reason: str | None = None
    cooldown_until: str | None = None
    observations_written: int = 0
    implausible: int = 0
    parser_mismatch: int = 0  # fetches whose lowest parsed price != the page's own cheapest
    budget_stop: bool = False  # stopped early at `max_requests`
    by_kind: Counter[str] = field(default_factory=Counter)
    sources_used: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.skipped:
            return f"SKIPPED (cooldown until {self.cooldown_until})"
        parts = [
            f"attempted={self.attempted}",
            f"succeeded={self.succeeded}",
            f"empty={self.empty}",
            f"failed={self.failed}",
            f"observations={self.observations_written}",
        ]
        if self.retries:
            parts.append(f"retries={self.retries}")
        if self.by_kind:
            parts.append(
                "failures=" + ",".join(f"{k}:{v}" for k, v in sorted(self.by_kind.items()))
            )
        if self.sources_used:
            parts.append(
                "via=" + ",".join(f"{k}:{v}" for k, v in sorted(self.sources_used.items()))
            )
        if self.implausible:
            parts.append(f"IMPLAUSIBLE_PRICES={self.implausible}")
        if self.parser_mismatch:
            parts.append(f"PARSER_MISMATCH={self.parser_mismatch}")
        if self.blocked:
            parts.append(f"ABORTED={self.abort_reason}")
        if self.cooldown_until:
            parts.append(f"cooldown_until={self.cooldown_until}")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Cooldown state
# ---------------------------------------------------------------------------


def cooldown_active(conn: sqlite3.Connection, now: datetime | None = None) -> str | None:
    """The ISO time the current cooldown ends, or None if we are clear to run."""
    now = now or clock_now()
    until = parse_ts(get_state(conn, STATE_COOLDOWN_UNTIL))
    return until.strftime("%Y-%m-%dT%H:%M:%SZ") if until and until > now else None


def enter_cooldown(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> str:
    """Escalate: 1st blocked run → cooldown_hours[0], 2nd → [1], … capped at the last."""
    now = now or clock_now()
    streak = int(get_state(conn, STATE_BLOCKED_STREAK, "0") or 0) + 1
    hours = cfg.fetch.cooldown_hours[min(streak, len(cfg.fetch.cooldown_hours)) - 1]
    until = (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_state(conn, STATE_BLOCKED_STREAK, str(streak))
    set_state(conn, STATE_COOLDOWN_UNTIL, until)
    return until


def clear_cooldown(conn: sqlite3.Connection) -> None:
    set_state(conn, STATE_BLOCKED_STREAK, "0")
    set_state(conn, STATE_COOLDOWN_UNTIL, None)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _due_cutoffs(now: datetime) -> dict[int, str]:
    return {prio: iso(now - timedelta(days=days)) for prio, days in PRIORITY_INTERVAL_DAYS.items()}


# ---------------------------------------------------------------------------
# The request budget
# ---------------------------------------------------------------------------

# Requests to these sources do not go to Google, so they do not spend the budget.
NOT_GOOGLE = ("serpapi",)
BUDGET_WINDOW = timedelta(hours=24)


def _budget_sql() -> str:
    marks = ",".join("?" * len(NOT_GOOGLE))
    return f"FROM fetch_attempts WHERE at > ? AND source NOT IN ({marks})"


def budget_used(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Google requests in the rolling 24 hours before `now` (every source but SerpApi:
    results pages, the fast-flights fallback, calendar calls and the canary)."""
    now = now or clock_now()
    row = conn.execute(
        f"SELECT COUNT(*) {_budget_sql()}", (iso(now - BUDGET_WINDOW), *NOT_GOOGLE)
    ).fetchone()
    return int(row[0])


def budget_remaining(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> int:
    """How many more Google requests the rolling 24-hour budget allows right now."""
    return max(0, cfg.collect.daily_request_budget - budget_used(conn, now))


def detail_fetches_per_day(cfg: Config) -> int:
    """Detail fetches a day leaves room for: the daily budget less the calendar sweep's
    calls (when it is on) and the canary's one request."""
    calendar = cfg.calendar.max_calls_per_run if cfg.calendar.enabled else 0
    return max(1, cfg.collect.daily_request_budget - calendar - 1)


def budget_frees_at(
    conn: sqlite3.Connection, cfg: Config, now: datetime | None = None
) -> datetime | None:
    """When the next request becomes allowed, if the budget is spent; None if it is not."""
    now = now or clock_now()
    used = budget_used(conn, now)
    over = used - cfg.collect.daily_request_budget
    if over < 0:
        return None
    row = conn.execute(
        f"SELECT at {_budget_sql()} ORDER BY at ASC LIMIT 1 OFFSET ?",
        (iso(now - BUDGET_WINDOW), *NOT_GOOGLE, over),
    ).fetchone()
    at = parse_ts(row[0]) if row else None
    return at + BUDGET_WINDOW if at else now


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

# Date pairs a person asked for (a fetch job): [{"id": query_id, "at": iso}, ...]. Each
# stays first in line until it has been fetched after it was asked for.
STATE_REQUESTED = "requested_queries"
# The calendar sweep's picks: {"at": iso, "ids": [...]}, fetched in the next batches.
STATE_SWEEP_FLAGGED = "sweep_flagged"
FLAG_HOURS = 24
REQUEST_EXPIRY_DAYS = 7


def _load_json(conn: sqlite3.Connection, key: str, default: Any) -> Any:
    try:
        return json.loads(get_state(conn, key) or "null") or default
    except ValueError:
        return default


def requested(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = _load_json(conn, STATE_REQUESTED, [])
    return [r for r in out if isinstance(r, dict) and "id" in r and "at" in r]


def request_queries(
    conn: sqlite3.Connection, query_ids: list[int], now: datetime | None = None
) -> None:
    """Put these date pairs first in line (a person asked for them)."""
    now = now or clock_now()
    stamp = iso(now)
    keep = [r for r in prune_requested(conn, now, save=False) if r["id"] not in set(query_ids)]
    set_state(
        conn,
        STATE_REQUESTED,
        json.dumps(keep + [{"id": int(q), "at": stamp} for q in query_ids]),
    )


def prune_requested(
    conn: sqlite3.Connection, now: datetime | None = None, save: bool = True
) -> list[dict[str, Any]]:
    """Drop requests that have been fetched since, are for inactive pairs, or are old."""
    now = now or clock_now()
    oldest = iso(now - timedelta(days=REQUEST_EXPIRY_DAYS))
    keep = []
    for r in requested(conn):
        row = conn.execute(
            "SELECT last_fetch_at, active FROM queries WHERE id = ?", (r["id"],)
        ).fetchone()
        if row is None or not row["active"] or r["at"] < oldest:
            continue
        if row["last_fetch_at"] is not None and row["last_fetch_at"] >= r["at"]:
            continue
        keep.append(r)
    if save:
        set_state(conn, STATE_REQUESTED, json.dumps(keep) if keep else None)
    return keep


def flag_from_sweep(
    conn: sqlite3.Connection, query_ids: list[int], now: datetime | None = None
) -> None:
    """Record the sweep's picks; the next batches confirm them with detail fetches."""
    now = now or clock_now()
    set_state(conn, STATE_SWEEP_FLAGGED, json.dumps({"at": iso(now), "ids": list(query_ids)}))


def _flagged(conn: sqlite3.Connection, now: datetime) -> tuple[list[int], str]:
    flagged = _load_json(conn, STATE_SWEEP_FLAGGED, {})
    at = flagged.get("at") if isinstance(flagged, dict) else None
    if not at or at < iso(now - timedelta(hours=FLAG_HOURS)):
        return [], ""
    return [int(i) for i in flagged.get("ids") or []], str(at)


def _top_search_dates(conn: sqlite3.Connection, today: str, per_search: int) -> list[int]:
    """The `per_search` cheapest date pairs of each active search, by their newest price
    (a detail fetch or a calendar estimate)."""
    if per_search <= 0:
        return []
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT query_id, price_cents, ROW_NUMBER() OVER (
                PARTITION BY query_id ORDER BY observed_at DESC, id DESC) AS rn
            FROM observations WHERE is_best = 1 OR source = 'google_calendar'
        ), ranked AS (
            SELECT wq.query_id, ROW_NUMBER() OVER (
                PARTITION BY wq.search_id ORDER BY l.price_cents ASC, q.depart_date ASC) AS rk
            FROM watch_queries wq
            JOIN searches s ON s.id = wq.search_id AND s.status = 'active'
            JOIN queries q ON q.id = wq.query_id AND q.active = 1 AND q.depart_date >= ?
            JOIN latest l ON l.query_id = wq.query_id AND l.rn = 1
        )
        SELECT DISTINCT query_id FROM ranked WHERE rk <= ?
        """,
        (today, per_search),
    ).fetchall()
    return [r[0] for r in rows]


def _due_query(row: sqlite3.Row) -> DueQuery:
    return DueQuery(
        query_id=row["id"],
        route=Route(
            dest=row["destination"],
            label=row["label"],
            target_price=row["target_price"],
            priority=row["priority"],
            max_stops=row["max_stops"],
            origin=row["origin"],
        ),
        depart_date=row["depart_date"],
        return_date=row["return_date"],
        pattern=row["pattern"],
        label=row["label"],
        last_fetch_at=row["last_fetch_at"],
    )


def _oldest_first(row: sqlite3.Row) -> tuple[int, str, int, str]:
    last = row["last_fetch_at"]
    return (0 if last is None else 1, last or "", row["priority"], row["depart_date"])


def select_due(
    conn: sqlite3.Connection,
    cfg: Config,
    now: datetime | None = None,
    limit: int | None = None,
    query_ids: list[int] | None = None,
) -> list[DueQuery]:
    """Pick the date pairs to fetch next, in this order:

      1. pairs a person asked for through a job (`request_queries`);
      2. active trips not fetched in `collect.trip_refresh_hours`;
      3. pairs the calendar sweep flagged in the last day and not fetched since;
      4. the `collect.top_dates_per_search` cheapest pairs of each search, not fetched
         in `collect.trip_refresh_hours`;
      5. everything else whose route-priority interval (`PRIORITY_INTERVAL_DAYS`, at
         most a week) has passed, never-fetched first, then oldest first.

    It is worked out from the database alone, so a block, a restart or a week of
    downtime changes nothing: the next batch looks at the same tables and carries on.
    `query_ids` replaces all of this with exactly those pairs (sweep confirmations).
    """
    now = now or clock_now()
    limit = limit or cfg.fetch.max_queries_per_run
    today = now.date().isoformat()

    extra = ""
    params: list[object] = [today]
    if query_ids:
        extra = f" AND q.id IN ({','.join('?' * len(query_ids))})"
        params += list(query_ids)
    rows = conn.execute(
        f"""
        SELECT q.id, q.depart_date, q.return_date, q.pattern, q.last_fetch_at,
               r.origin, r.destination, r.label, r.target_price, r.priority, r.max_stops
        FROM queries q
        JOIN routes r ON r.id = q.route_id
        WHERE q.active = 1 AND r.active = 1 AND q.depart_date >= ?{extra}
        """,
        params,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}

    if query_ids:
        picked = sorted(rows, key=lambda r: (r["priority"], *_oldest_first(r)))
        return [_due_query(r) for r in picked[:limit]]

    def stale_since(cutoff: str) -> Callable[[int], bool]:
        return lambda qid: (by_id[qid]["last_fetch_at"] or "") < cutoff

    refresh_cut = iso(now - timedelta(hours=cfg.collect.trip_refresh_hours))
    order: list[int] = []
    seen: set[int] = set()

    def take(ids: list[int], keep: Callable[[int], bool], sort: bool = True) -> None:
        chosen = [i for i in ids if i in by_id and i not in seen and keep(i)]
        if sort:
            chosen.sort(key=lambda i: _oldest_first(by_id[i]))
        for i in chosen:
            seen.add(i)
            order.append(i)

    # 1. asked for by a person, in the order asked
    asked = {r["id"]: r["at"] for r in requested(conn)}
    take(list(asked), lambda i: (by_id[i]["last_fetch_at"] or "") < asked[i], sort=False)
    # 2. active trips
    trip_ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT wq.query_id FROM watch_queries wq "
            "JOIN trips t ON t.id = wq.trip_id WHERE t.status = 'active'"
        )
    ]
    take(trip_ids, stale_since(refresh_cut))
    # 3. flagged by today's sweep
    flagged, flagged_at = _flagged(conn, now)
    take(flagged, stale_since(flagged_at) if flagged_at else lambda i: False, sort=False)
    # 4. the cheapest few dates of each search
    take(_top_search_dates(conn, today, cfg.collect.top_dates_per_search), stale_since(refresh_cut))
    # 5. the rotation
    cutoffs = _due_cutoffs(now)
    take(
        list(by_id),
        lambda i: (
            by_id[i]["last_fetch_at"] is None
            or by_id[i]["last_fetch_at"] <= cutoffs.get(by_id[i]["priority"], cutoffs[2])
        ),
    )
    return [_due_query(by_id[i]) for i in order[:limit]]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def store(
    conn: sqlite3.Connection, query_id: int, offers: list[Offer], is_best_series: bool = True
) -> tuple[int, int]:
    """Append observations for one fetch. Returns (written, implausible).

    `is_best_series=False` records rows that must not join the canonical
    per-fetch-minimum series (calendar sweeps): they are kept for context but
    never become a query's "current price".
    """
    observed_at = utcnow()
    written = implausible = 0
    for i, offer in enumerate(sorted(offers, key=lambda o: o.price_cents)):
        if not price_is_plausible(offer.price_cents):
            implausible += 1
        conn.execute(
            """
            INSERT INTO observations
                (query_id, observed_at, price_cents, currency, airline, stops,
                 duration_min, is_best, raw, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query_id,
                observed_at,
                offer.price_cents,
                offer.currency,
                offer.airline,
                offer.stops,
                offer.duration_min,
                1 if (i == 0 and is_best_series) else 0,
                offer.raw_json(),
                offer.source or None,
            ),
        )
        written += 1
    return written, implausible


def log_attempt(
    conn: sqlite3.Connection,
    run_id: int | None,
    query_id: int | None,
    source: str,
    outcome: str,
    latency_ms: int | None,
    error: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO fetch_attempts (run_id, query_id, at, source, outcome, latency_ms, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, query_id, utcnow(), source, outcome, latency_ms, error),
    )


def _source_used(source: Source) -> str:
    return str(getattr(source, "last_used", None) or getattr(source, "name", "?"))


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run(
    conn: sqlite3.Connection,
    cfg: Config,
    source: Source,
    now: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    limit: int | None = None,
    verbose: bool = True,
    respect_cooldown: bool = True,
    query_ids: list[int] | None = None,
    kind: str = "fetch",
    max_requests: int | None = None,
) -> FetchStats:
    """Execute one fetch run, recording it in `run_log` whatever happens.

    `max_requests` caps the requests this run may make, retries included (the worker
    passes what is left of the rolling 24-hour budget); the run stops before it would
    go over.
    """
    stats = FetchStats()
    now = now or clock_now()
    cur = conn.execute("INSERT INTO run_log (started_at, kind) VALUES (?, ?)", (utcnow(), kind))
    stats.run_id = cur.lastrowid
    conn.commit()

    until = cooldown_active(conn, now) if respect_cooldown else None
    if until:
        stats.skipped, stats.cooldown_until = True, until
        conn.execute(
            "UPDATE run_log SET finished_at = ?, skipped = 1, notes = ? WHERE id = ?",
            (utcnow(), f"skipped: in cooldown until {until}", stats.run_id),
        )
        conn.commit()
        if verbose:
            log.warning(
                f"[fetch] run {stats.run_id}: skipped — cooldown until {until} (a previous run "
                "was blocked)"
            )
        return stats

    due = select_due(conn, cfg, now=now, limit=limit, query_ids=query_ids)
    if verbose:
        log.info(
            f"[fetch] run {stats.run_id}: {len(due)} queries due (cap "
            f"{limit or cfg.fetch.max_queries_per_run}) via {getattr(source, 'name', '?')}"
        )

    consecutive_failures = 0
    note_bits: list[str] = []
    threshold = cfg.fetch.abort_after_consecutive_failures
    requests = 0

    for i, item in enumerate(due):
        if max_requests is not None and requests >= max_requests:
            stats.budget_stop = True
            note_bits.append(f"stopped at the request budget after {requests} request(s)")
            break
        # Jittered sleep BETWEEN calls — fixed intervals are exactly the
        # signature a rate limiter looks for.
        if i > 0:
            sleeper(random.uniform(cfg.fetch.min_sleep_seconds, cfg.fetch.max_sleep_seconds))

        stats.attempted += 1
        leg = f"{item.route.origin_or(cfg.home)}→{item.route.dest} {item.depart_date}"
        if item.return_date:
            leg += f"/{item.return_date}"

        offers: list[Offer] | None = None
        failure: SourceError | None = None
        empty: NoResults | None = None
        retried = False
        while True:
            requests += 1
            t0 = time.monotonic()
            try:
                offers = source.fetch(
                    cfg, item.route, item.depart_date, item.return_date, cfg.fetch.offers_per_fetch
                )
                latency = int((time.monotonic() - t0) * 1000)
                log_attempt(conn, stats.run_id, item.query_id, _source_used(source), "ok", latency)
                break
            except NoResults as exc:
                latency = int((time.monotonic() - t0) * 1000)
                log_attempt(
                    conn,
                    stats.run_id,
                    item.query_id,
                    _source_used(source),
                    "empty",
                    latency,
                    str(exc),
                )
                empty = exc
                break
            except SourceError as exc:
                latency = int((time.monotonic() - t0) * 1000)
                log_attempt(
                    conn,
                    stats.run_id,
                    item.query_id,
                    _source_used(source),
                    exc.kind,
                    latency,
                    str(exc),
                )
                budget_left = max_requests is None or requests < max_requests
                if (
                    isinstance(exc, NetworkError)
                    and cfg.fetch.retry_network_once
                    and not retried
                    and budget_left
                ):
                    # Transient by definition; one retry after a longer pause.
                    retried = True
                    stats.retries += 1
                    if verbose:
                        log.warning(
                            f"[retry] {leg}: {exc} — retrying once in "
                            f"{cfg.fetch.retry_sleep_seconds:.0f}s"
                        )
                    sleeper(cfg.fetch.retry_sleep_seconds)
                    continue
                failure = exc
                break

        if empty is not None:
            # Google answered, there is just no inventory. Proof we are NOT
            # blocked, so the consecutive-failure counter resets.
            consecutive_failures = 0
            stats.empty += 1
            conn.execute(
                "UPDATE queries SET last_fetch_at = ? WHERE id = ?", (utcnow(), item.query_id)
            )
            conn.commit()
            if verbose:
                log.info(f"[empty] {leg}: {empty}")
            continue

        if failure is not None:
            consecutive_failures += 1
            stats.failed += 1
            stats.by_kind[failure.kind] += 1
            stats.errors.append(f"{leg}: {failure.kind}: {failure}")
            conn.commit()
            if verbose:
                log.warning(f"[FAIL:{failure.kind}] {leg}: {failure}")

            # A blocked signal is explicit — stop on the first one. Anything
            # else needs `threshold` in a row before we give up on the run.
            if isinstance(failure, BlockedError) or consecutive_failures >= threshold:
                stats.blocked = True
                stats.abort_reason = failure.kind
                note = (
                    f"aborted after {consecutive_failures} consecutive failures [{failure.kind}]: "
                    f"{failure}"
                )
                if not isinstance(failure, LayoutError):
                    # Rate limit or network: back off, escalating. A layout
                    # change is not helped by waiting, so no cooldown for it.
                    stats.cooldown_until = enter_cooldown(conn, cfg, now)
                    note += f"; cooldown until {stats.cooldown_until}"
                note_bits.append(note)
                if verbose:
                    log.warning(f"[ABORT] {note}")
                break
            continue

        assert offers is not None
        consecutive_failures = 0
        written, implausible = store(conn, item.query_id, offers)
        stats.succeeded += 1
        stats.sources_used[_source_used(source)] += 1
        stats.observations_written += written
        stats.implausible += implausible
        mismatch = any("page_min" in o.raw for o in offers)
        stats.parser_mismatch += 1 if mismatch else 0
        conn.execute("UPDATE queries SET last_fetch_at = ? WHERE id = ?", (utcnow(), item.query_id))
        conn.commit()

        if verbose:
            best = min(o.price_cents for o in offers)
            flag = "  ⚠ implausible price — check units" if implausible else ""
            if mismatch:
                flag += f"  ⚠ page says cheapest is {offers[0].raw['page_min']} — parser check"
            log.info(f"[ok] {leg}: ${best / 100:,.0f} ({written} offers){flag}")

    if stats.succeeded and not stats.blocked:
        clear_cooldown(conn)
        set_state(conn, STATE_LAST_SUCCESS, utcnow())
    elif stats.succeeded:
        set_state(conn, STATE_LAST_SUCCESS, utcnow())

    if stats.implausible:
        note_bits.append(
            f"{stats.implausible} observation(s) outside the plausible price band — "
            "upstream price units may have changed; see raw JSON"
        )
    if stats.parser_mismatch:
        note_bits.append(
            f"{stats.parser_mismatch} fetch(es) where the page's own cheapest price differed from "
            "the "
            "lowest parsed price — see raw.page_min"
        )
    if stats.by_kind.get("layout"):
        note_bits.append(f"{stats.by_kind['layout']} layout failure(s) — see {cfg.failure_dir}")

    conn.execute(
        """
        UPDATE run_log
           SET finished_at = ?, attempted = ?, succeeded = ?, failed = ?, blocked = ?, notes = ?
         WHERE id = ?
        """,
        (
            utcnow(),
            stats.attempted,
            stats.succeeded,
            stats.failed,
            1 if stats.blocked else 0,
            "; ".join(note_bits) if note_bits else None,
            stats.run_id,
        ),
    )
    conn.commit()
    return stats
