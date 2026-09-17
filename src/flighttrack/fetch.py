"""Read due queries, call the source, append observations.

Knows nothing about alerting. The two must be able to fail independently: a
broken notification channel should never stop history from accumulating, and a
blocked scraper should never silently look like "no deals today".

Pacing here is deliberately conservative (plan §7). The binding constraint on
this whole system is an undocumented per-IP limit at Google, so the rule is:
serial, jittered, capped, and never retry into a failure.
"""

from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .config import Config, Route
from .db import utcnow
from .source import NoResults, Offer, Source, SourceError, price_is_plausible

# How stale a query may get before it is due again, by route priority.
PRIORITY_INTERVAL_DAYS = {1: 1, 2: 3, 3: 7}


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
    blocked: bool = False
    observations_written: int = 0
    implausible: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"attempted={self.attempted}",
            f"succeeded={self.succeeded}",
            f"empty={self.empty}",
            f"failed={self.failed}",
            f"observations={self.observations_written}",
        ]
        if self.implausible:
            parts.append(f"IMPLAUSIBLE_PRICES={self.implausible}")
        if self.blocked:
            parts.append("BLOCKED=1")
        return " ".join(parts)


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
) -> list[DueQuery]:
    """Pick the queries to fetch this run.

    The full date grid is larger than any single run may safely cover — 378
    queries against a 60/run cap, with the current config. So rather than
    fetching everything (impossible) or shrinking the grid (throws away the
    price surface that is the point of the system), this rotates:

      1. only queries whose route-priority interval has elapsed are due,
      2. due queries are ordered by priority, then by how long they have gone
         unfetched, then by how soon they depart,
      3. the run takes the first `max_queries_per_run` of those.

    Full coverage therefore takes several days rather than one, and no query
    can starve — every skipped query sorts higher on the next run.
    """
    now = now or datetime.now(timezone.utc)
    limit = limit or cfg.fetch.max_queries_per_run
    cutoffs = _due_cutoffs(now)
    today = now.date().isoformat()

    rows = conn.execute(
        """
        SELECT q.id, q.depart_date, q.return_date, q.pattern, q.last_fetch_at,
               r.destination, r.label, r.target_price, r.priority, r.max_stops
        FROM queries q
        JOIN routes r ON r.id = q.route_id
        WHERE q.active = 1 AND r.active = 1 AND q.depart_date >= ?
        ORDER BY
            r.priority ASC,
            CASE WHEN q.last_fetch_at IS NULL THEN 0 ELSE 1 END ASC,
            q.last_fetch_at ASC,
            q.depart_date ASC
        """,
        (today,),
    ).fetchall()

    due: list[DueQuery] = []
    for row in rows:
        cutoff = cutoffs.get(row["priority"], cutoffs[2])
        if row["last_fetch_at"] is not None and row["last_fetch_at"] > cutoff:
            continue  # fetched recently enough for its priority
        due.append(
            DueQuery(
                query_id=row["id"],
                route=Route(
                    dest=row["destination"],
                    label=row["label"],
                    target_price=row["target_price"],
                    priority=row["priority"],
                    max_stops=row["max_stops"],
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


def _store(conn: sqlite3.Connection, query_id: int, offers: list[Offer]) -> tuple[int, int]:
    """Append observations for one fetch. Returns (written, implausible)."""
    observed_at = utcnow()
    written = 0
    implausible = 0

    # Cheapest first; `is_best` flags the minimum of THIS fetch. Storing the
    # runners-up is what lets you later ask "was the cheap one a 2-stop redeye?"
    for i, offer in enumerate(sorted(offers, key=lambda o: o.price_cents)):
        if not price_is_plausible(offer.price_cents):
            implausible += 1
        conn.execute(
            """
            INSERT INTO observations
                (query_id, observed_at, price_cents, currency, airline, stops,
                 duration_min, is_best, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query_id,
                observed_at,
                offer.price_cents,
                offer.currency,
                offer.airline,
                offer.stops,
                offer.duration_min,
                1 if i == 0 else 0,
                offer.raw_json(),
            ),
        )
        written += 1
    return written, implausible


def run(
    conn: sqlite3.Connection,
    cfg: Config,
    source: Source,
    now: datetime | None = None,
    sleeper=time.sleep,
    limit: int | None = None,
    verbose: bool = True,
) -> FetchStats:
    """Execute one fetch run, recording it in `run_log` whatever happens."""
    stats = FetchStats()
    started = utcnow()
    cur = conn.execute("INSERT INTO run_log (started_at) VALUES (?)", (started,))
    stats.run_id = cur.lastrowid
    conn.commit()

    due = select_due(conn, cfg, now=now, limit=limit)
    if verbose:
        print(f"[fetch] run {stats.run_id}: {len(due)} queries due (cap {limit or cfg.fetch.max_queries_per_run})")

    consecutive_failures = 0
    note_bits: list[str] = []

    for i, item in enumerate(due):
        # Jittered sleep BETWEEN calls — fixed intervals are exactly the
        # signature a rate limiter looks for.
        if i > 0:
            sleeper(random.uniform(cfg.fetch.min_sleep_seconds, cfg.fetch.max_sleep_seconds))

        stats.attempted += 1
        leg = f"{cfg.home}→{item.route.dest} {item.depart_date}"
        if item.return_date:
            leg += f"/{item.return_date}"

        try:
            offers = source.fetch(
                cfg, item.route, item.depart_date, item.return_date, cfg.fetch.offers_per_fetch
            )
        except NoResults as exc:
            # Google answered, there is just no inventory. Proof we are NOT
            # blocked, so the consecutive-failure counter resets.
            consecutive_failures = 0
            stats.empty += 1
            conn.execute(
                "UPDATE queries SET last_fetch_at = ? WHERE id = ?", (utcnow(), item.query_id)
            )
            conn.commit()
            if verbose:
                print(f"  [empty] {leg}: {exc}")
            continue
        except SourceError as exc:
            consecutive_failures += 1
            stats.failed += 1
            stats.errors.append(f"{leg}: {exc}")
            if verbose:
                print(f"  [FAIL] {leg}: {exc}")

            if consecutive_failures >= cfg.fetch.abort_after_consecutive_failures:
                # Never retry into a rate limit — it extends the lockout on a
                # sliding-window limiter. Abort and let the next run cool off.
                stats.blocked = True
                note_bits.append(
                    f"aborted after {consecutive_failures} consecutive failures: {exc}"
                )
                if verbose:
                    print(f"  [ABORT] {consecutive_failures} consecutive failures — stopping run")
                break
            continue

        consecutive_failures = 0
        written, implausible = _store(conn, item.query_id, offers)
        stats.succeeded += 1
        stats.observations_written += written
        stats.implausible += implausible
        conn.execute("UPDATE queries SET last_fetch_at = ? WHERE id = ?", (utcnow(), item.query_id))
        conn.commit()

        if verbose:
            best = min(o.price_cents for o in offers)
            flag = "  ⚠ implausible price — check units" if implausible else ""
            print(f"  [ok] {leg}: ${best/100:,.0f} ({written} offers){flag}")

    if stats.implausible:
        note_bits.append(
            f"{stats.implausible} observation(s) outside the plausible price band — "
            "upstream price units may have changed; see raw JSON"
        )

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
