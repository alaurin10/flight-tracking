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

import random
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import Config, Route
from .db import get_state, parse_ts, set_state, utcnow
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
    by_kind: Counter = field(default_factory=Counter)
    sources_used: Counter = field(default_factory=Counter)
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
            parts.append("failures=" + ",".join(f"{k}:{v}" for k, v in sorted(self.by_kind.items())))
        if self.sources_used:
            parts.append("via=" + ",".join(f"{k}:{v}" for k, v in sorted(self.sources_used.items())))
        if self.implausible:
            parts.append(f"IMPLAUSIBLE_PRICES={self.implausible}")
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
    now = now or datetime.now(timezone.utc)
    until = parse_ts(get_state(conn, STATE_COOLDOWN_UNTIL))
    return until.strftime("%Y-%m-%dT%H:%M:%SZ") if until and until > now else None


def enter_cooldown(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> str:
    """Escalate: 1st blocked run → cooldown_hours[0], 2nd → [1], … capped at the last."""
    now = now or datetime.now(timezone.utc)
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
    return {
        prio: (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for prio, days in PRIORITY_INTERVAL_DAYS.items()
    }


def select_due(
    conn: sqlite3.Connection,
    cfg: Config,
    now: datetime | None = None,
    limit: int | None = None,
    query_ids: list[int] | None = None,
) -> list[DueQuery]:
    """Pick the queries to fetch this run.

    The grid is larger than one run may safely cover, so this rotates:
      1. only queries whose route-priority interval has elapsed are due,
      2. due queries are ordered by priority, then by how long they have gone
         unfetched, then by how soon they depart,
      3. the run takes the first `max_queries_per_run` of those.
    `query_ids` bypasses the due test — used to confirm sweep candidates.
    """
    now = now or datetime.now(timezone.utc)
    limit = limit or cfg.fetch.max_queries_per_run
    cutoffs = _due_cutoffs(now)
    today = now.date().isoformat()

    extra = ""
    params: list[object] = [today]
    if query_ids:
        extra = f" AND q.id IN ({','.join('?' * len(query_ids))})"
        params += list(query_ids)

    rows = conn.execute(
        f"""
        SELECT q.id, q.depart_date, q.return_date, q.pattern, q.last_fetch_at,
               r.destination, r.label, r.target_price, r.priority, r.max_stops
        FROM queries q
        JOIN routes r ON r.id = q.route_id
        WHERE q.active = 1 AND r.active = 1 AND q.depart_date >= ?{extra}
        ORDER BY
            r.priority ASC,
            CASE WHEN q.last_fetch_at IS NULL THEN 0 ELSE 1 END ASC,
            q.last_fetch_at ASC,
            q.depart_date ASC
        """,
        params,
    ).fetchall()

    due: list[DueQuery] = []
    for row in rows:
        cutoff = cutoffs.get(row["priority"], cutoffs[2])
        if not query_ids and row["last_fetch_at"] is not None and row["last_fetch_at"] > cutoff:
            continue  # fetched recently enough for its priority
        due.append(
            DueQuery(
                query_id=row["id"],
                route=Route(
                    dest=row["destination"], label=row["label"], target_price=row["target_price"],
                    priority=row["priority"], max_stops=row["max_stops"],
                ),
                depart_date=row["depart_date"],
                return_date=row["return_date"],
                pattern=row["pattern"],
                label=row["label"],
                last_fetch_at=row["last_fetch_at"],
            )
        )
        if len(due) >= limit:
            break
    return due


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def store(conn: sqlite3.Connection, query_id: int, offers: list[Offer], is_best_series: bool = True) -> tuple[int, int]:
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
                query_id, observed_at, offer.price_cents, offer.currency, offer.airline,
                offer.stops, offer.duration_min, 1 if (i == 0 and is_best_series) else 0,
                offer.raw_json(), offer.source or None,
            ),
        )
        written += 1
    return written, implausible


def log_attempt(conn, run_id, query_id, source, outcome, latency_ms, error=None) -> None:
    conn.execute(
        "INSERT INTO fetch_attempts (run_id, query_id, at, source, outcome, latency_ms, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, query_id, utcnow(), source, outcome, latency_ms, error),
    )


def _source_used(source: Source) -> str:
    return getattr(source, "last_used", None) or getattr(source, "name", "?")


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run(
    conn: sqlite3.Connection,
    cfg: Config,
    source: Source,
    now: datetime | None = None,
    sleeper=time.sleep,
    limit: int | None = None,
    verbose: bool = True,
    respect_cooldown: bool = True,
    query_ids: list[int] | None = None,
    kind: str = "fetch",
) -> FetchStats:
    """Execute one fetch run, recording it in `run_log` whatever happens."""
    stats = FetchStats()
    now = now or datetime.now(timezone.utc)
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
            print(f"[fetch] run {stats.run_id}: skipped — cooldown until {until} (a previous run was blocked)")
        return stats

    due = select_due(conn, cfg, now=now, limit=limit, query_ids=query_ids)
    if verbose:
        print(f"[fetch] run {stats.run_id}: {len(due)} queries due (cap {limit or cfg.fetch.max_queries_per_run}) via {getattr(source, 'name', '?')}")

    consecutive_failures = 0
    note_bits: list[str] = []
    threshold = cfg.fetch.abort_after_consecutive_failures

    for i, item in enumerate(due):
        # Jittered sleep BETWEEN calls — fixed intervals are exactly the
        # signature a rate limiter looks for.
        if i > 0:
            sleeper(random.uniform(cfg.fetch.min_sleep_seconds, cfg.fetch.max_sleep_seconds))

        stats.attempted += 1
        leg = f"{cfg.home}→{item.route.dest} {item.depart_date}"
        if item.return_date:
            leg += f"/{item.return_date}"

        offers: list[Offer] | None = None
        failure: SourceError | None = None
        empty: NoResults | None = None
        retried = False
        while True:
            t0 = time.monotonic()
            try:
                offers = source.fetch(cfg, item.route, item.depart_date, item.return_date, cfg.fetch.offers_per_fetch)
                latency = int((time.monotonic() - t0) * 1000)
                log_attempt(conn, stats.run_id, item.query_id, _source_used(source), "ok", latency)
                break
            except NoResults as exc:
                latency = int((time.monotonic() - t0) * 1000)
                log_attempt(conn, stats.run_id, item.query_id, _source_used(source), "empty", latency, str(exc))
                empty = exc
                break
            except SourceError as exc:
                latency = int((time.monotonic() - t0) * 1000)
                log_attempt(conn, stats.run_id, item.query_id, _source_used(source), exc.kind, latency, str(exc))
                if isinstance(exc, NetworkError) and cfg.fetch.retry_network_once and not retried:
                    # Transient by definition; one retry after a longer pause.
                    retried = True
                    stats.retries += 1
                    if verbose:
                        print(f"  [retry] {leg}: {exc} — retrying once in {cfg.fetch.retry_sleep_seconds:.0f}s")
                    sleeper(cfg.fetch.retry_sleep_seconds)
                    continue
                failure = exc
                break

        if empty is not None:
            # Google answered, there is just no inventory. Proof we are NOT
            # blocked, so the consecutive-failure counter resets.
            consecutive_failures = 0
            stats.empty += 1
            conn.execute("UPDATE queries SET last_fetch_at = ? WHERE id = ?", (utcnow(), item.query_id))
            conn.commit()
            if verbose:
                print(f"  [empty] {leg}: {empty}")
            continue

        if failure is not None:
            consecutive_failures += 1
            stats.failed += 1
            stats.by_kind[failure.kind] += 1
            stats.errors.append(f"{leg}: {failure.kind}: {failure}")
            conn.commit()
            if verbose:
                print(f"  [FAIL:{failure.kind}] {leg}: {failure}")

            # A blocked signal is explicit — stop on the first one. Anything
            # else needs `threshold` in a row before we give up on the run.
            if isinstance(failure, BlockedError) or consecutive_failures >= threshold:
                stats.blocked = True
                stats.abort_reason = failure.kind
                note = f"aborted after {consecutive_failures} consecutive failures [{failure.kind}]: {failure}"
                if not isinstance(failure, LayoutError):
                    # Rate limit or network: back off, escalating. A layout
                    # change is not helped by waiting, so no cooldown for it.
                    stats.cooldown_until = enter_cooldown(conn, cfg, now)
                    note += f"; cooldown until {stats.cooldown_until}"
                note_bits.append(note)
                if verbose:
                    print(f"  [ABORT] {note}")
                break
            continue

        assert offers is not None
        consecutive_failures = 0
        written, implausible = store(conn, item.query_id, offers)
        stats.succeeded += 1
        stats.sources_used[_source_used(source)] += 1
        stats.observations_written += written
        stats.implausible += implausible
        conn.execute("UPDATE queries SET last_fetch_at = ? WHERE id = ?", (utcnow(), item.query_id))
        conn.commit()

        if verbose:
            best = min(o.price_cents for o in offers)
            flag = "  ⚠ implausible price — check units" if implausible else ""
            print(f"  [ok] {leg}: ${best/100:,.0f} ({written} offers){flag}")

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
    if stats.by_kind.get("layout"):
        note_bits.append(f"{stats.by_kind['layout']} layout failure(s) — see {cfg.failure_dir}")

    conn.execute(
        """
        UPDATE run_log
           SET finished_at = ?, attempted = ?, succeeded = ?, failed = ?, blocked = ?, notes = ?
         WHERE id = ?
        """,
        (
            utcnow(), stats.attempted, stats.succeeded, stats.failed,
            1 if stats.blocked else 0, "; ".join(note_bits) if note_bits else None, stats.run_id,
        ),
    )
    conn.commit()
    return stats
