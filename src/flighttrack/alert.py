"""Decide what is notable and send it. Knows nothing about fetching.

Two-phase by design (plan §8). A fixed price threshold ages badly — set $650
for Tokyo and it may never fire, and a system that never fires is a system you
forget you installed. But a threshold is the only thing available before
history exists, so:

  * Phase 1 (weeks 0–4): absolute target price, or a genuine all-time low.
  * Phase 2 (≥30 days of history): at or below the Nth percentile of the
    ROUTE's trailing-60-day prices. Route-level baselines are far more robust
    than per-date-pair ones, which have too few samples to mean anything.

Phase 1 is expected to be QUIET. That is the system accumulating, not failing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import utcnow
from .notify import NotifyError, notify
from .report import fmt_date, fmt_money


@dataclass
class Candidate:
    query_id: int
    observation_id: int
    dest: str
    label: str
    pattern: str
    depart_date: str
    return_date: str | None
    price_cents: int
    airline: str | None
    stops: int | None
    reason: str
    context: str
    deep_link: str | None

    def line(self) -> str:
        bits = [
            f"{self.label} ({self.dest})",
            f"{fmt_date(self.depart_date)} → {fmt_date(self.return_date)}",
            fmt_money(self.price_cents),
        ]
        if self.airline:
            bits.append(self.airline)
        if self.stops is not None:
            bits.append(f"{self.stops} stop{'s' if self.stops != 1 else ''}")
        return " · ".join(bits) + f"\n  {self.context}"


@dataclass
class AlertStats:
    considered: int = 0
    qualified: int = 0
    suppressed_dedup: int = 0
    sent: int = 0
    digested: int = 0
    delivery_error: str | None = None

    def summary(self) -> str:
        s = (
            f"considered={self.considered} qualified={self.qualified} "
            f"deduped={self.suppressed_dedup} sent={self.sent}"
        )
        if self.digested:
            s += f" digested={self.digested}"
        if self.delivery_error:
            s += f" DELIVERY_ERROR={self.delivery_error}"
        return s


def _iso_ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def percentile(values: list[int], p: float) -> int | None:
    """Nearest-rank percentile. Simple, dependency-free, good enough here."""
    if not values:
        return None
    ordered = sorted(values)
    import math

    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def route_history_days(conn: sqlite3.Connection, route_id: int) -> float:
    """How many days of observations exist for a route, oldest to newest."""
    row = conn.execute(
        """
        SELECT MIN(o.observed_at) AS first, MAX(o.observed_at) AS last
        FROM observations o JOIN queries q ON q.id = o.query_id
        WHERE q.route_id = ?
        """,
        (route_id,),
    ).fetchone()
    if not row or not row["first"] or not row["last"]:
        return 0.0
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    try:
        delta = datetime.strptime(row["last"], fmt) - datetime.strptime(row["first"], fmt)
    except ValueError:
        return 0.0
    return delta.total_seconds() / 86400.0


def route_percentile(conn: sqlite3.Connection, route_id: int, cfg: Config) -> int | None:
    """The Nth-percentile price for a route over its trailing window."""
    cutoff = _iso_ago(days=cfg.alerts.percentile_window_days)
    prices = [
        r["price_cents"]
        for r in conn.execute(
            """
            SELECT o.price_cents FROM observations o
            JOIN queries q ON q.id = o.query_id
            WHERE q.route_id = ? AND o.is_best = 1 AND o.observed_at >= ?
            """,
            (route_id, cutoff),
        )
    ]
    return percentile(prices, cfg.alerts.percentile)


def _prior_stats(conn: sqlite3.Connection, query_id: int, observation_id: int) -> tuple[int, int | None]:
    """Count and minimum of observations for a query BEFORE the given one."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n, MIN(price_cents) AS lo
        FROM observations
        WHERE query_id = ? AND is_best = 1 AND id < ?
        """,
        (query_id, observation_id),
    ).fetchone()
    return row["n"], row["lo"]


def find_candidates(conn: sqlite3.Connection, cfg: Config) -> tuple[list[Candidate], int]:
    """Evaluate the newest observation of each active query. Returns (candidates, considered)."""
    fresh_cutoff = _iso_ago(hours=cfg.alerts.consider_observations_within_hours)

    rows = conn.execute(
        """
        WITH latest AS (
            SELECT o.*, ROW_NUMBER() OVER (
                       PARTITION BY o.query_id ORDER BY o.observed_at DESC, o.id DESC
                   ) AS rn
            FROM observations o
            WHERE o.is_best = 1 AND o.observed_at >= ?
        )
        SELECT l.id AS observation_id, l.query_id, l.price_cents, l.airline, l.stops,
               q.depart_date, q.return_date, q.pattern, q.deep_link,
               r.id AS route_id, r.destination, r.label, r.target_price
        FROM latest l
        JOIN queries q ON q.id = l.query_id
        JOIN routes r ON r.id = q.route_id
        WHERE l.rn = 1 AND q.active = 1 AND r.active = 1
        ORDER BY l.price_cents ASC
        """,
        (fresh_cutoff,),
    ).fetchall()

    phase2_cache: dict[int, int | None] = {}
    candidates: list[Candidate] = []

    for row in rows:
        price = row["price_cents"]
        reasons: list[tuple[str, str]] = []

        # --- absolute threshold -------------------------------------------
        target = row["target_price"]
        if target and price <= target:
            reasons.append(("threshold", f"at or below target of {fmt_money(target)}"))

        # --- all-time low -------------------------------------------------
        n_prior, prior_low = _prior_stats(conn, row["query_id"], row["observation_id"])
        if (
            n_prior >= cfg.alerts.min_observations_for_all_time_low
            and prior_low is not None
            and price < prior_low
        ):
            drop = (prior_low - price) / prior_low * 100.0
            reasons.append(
                ("all_time_low", f"all-time low — {drop:.0f}% under previous best {fmt_money(prior_low)}")
            )

        # --- phase 2 percentile -------------------------------------------
        if cfg.alerts.percentile_phase2_enabled:
            route_id = row["route_id"]
            if route_id not in phase2_cache:
                have = route_history_days(conn, route_id)
                phase2_cache[route_id] = (
                    route_percentile(conn, route_id, cfg)
                    if have >= cfg.alerts.history_days_required
                    else None
                )
            threshold = phase2_cache[route_id]
            if threshold is not None and price <= threshold:
                reasons.append(
                    (
                        "percentile",
                        f"in the cheapest {cfg.alerts.percentile}% of "
                        f"{row['destination']} fares over {cfg.alerts.percentile_window_days} "
                        f"days (p{cfg.alerts.percentile} = {fmt_money(threshold)})",
                    )
                )

        if not reasons:
            continue

        # Most specific reason wins for the record; all are shown to the reader.
        order = {"all_time_low": 0, "percentile": 1, "threshold": 2}
        reasons.sort(key=lambda r: order.get(r[0], 9))
        candidates.append(
            Candidate(
                query_id=row["query_id"],
                observation_id=row["observation_id"],
                dest=row["destination"],
                label=row["label"],
                pattern=row["pattern"],
                depart_date=row["depart_date"],
                return_date=row["return_date"],
                price_cents=price,
                airline=row["airline"],
                stops=row["stops"],
                reason=reasons[0][0],
                context="; ".join(r[1] for r in reasons),
                deep_link=row["deep_link"],
            )
        )

    return candidates, len(rows)


def _passes_dedup(conn: sqlite3.Connection, cand: Candidate, cfg: Config) -> bool:
    """Suppress repeats. Without this the system becomes noise and gets muted."""
    cutoff = _iso_ago(days=cfg.alerts.dedup_days)
    row = conn.execute(
        """
        SELECT price_cents FROM alerts_sent
        WHERE query_id = ? AND sent_at >= ?
        ORDER BY sent_at DESC LIMIT 1
        """,
        (cand.query_id, cutoff),
    ).fetchone()
    if row is None:
        return True

    # Already alerted recently — only a materially further drop gets through.
    required = row["price_cents"] * (1 - cfg.alerts.dedup_further_drop_pct / 100.0)
    return cand.price_cents <= required


def _record(conn: sqlite3.Connection, cands: list[Candidate]) -> None:
    now = utcnow()
    for c in cands:
        conn.execute(
            """
            INSERT INTO alerts_sent (query_id, observation_id, sent_at, reason, price_cents)
            VALUES (?, ?, ?, ?, ?)
            """,
            (c.query_id, c.observation_id, now, c.reason, c.price_cents),
        )
    conn.commit()


def run(conn: sqlite3.Connection, cfg: Config, dry_run: bool = False, verbose: bool = True) -> AlertStats:
    """Evaluate fresh observations and deliver what qualifies."""
    stats = AlertStats()
    if not cfg.alerts.enabled:
        if verbose:
            print("[alert] disabled in config")
        return stats

    candidates, considered = find_candidates(conn, cfg)
    stats.considered = considered
    stats.qualified = len(candidates)

    passing = []
    for c in candidates:
        if _passes_dedup(conn, c, cfg):
            passing.append(c)
        else:
            stats.suppressed_dedup += 1

    if verbose:
        print(f"[alert] {stats.summary()}")
    if not passing:
        if verbose:
            print("[alert] nothing to send")
        return stats

    try:
        if len(passing) <= cfg.alerts.max_per_run:
            # Few enough to send individually, each with its own deep link.
            for c in passing:
                if dry_run:
                    print(f"  [dry-run] {c.line()}")
                else:
                    notify(
                        cfg.notify,
                        f"✈ {fmt_money(c.price_cents)} {c.label} · {fmt_date(c.depart_date)}",
                        c.line(),
                        c.deep_link,
                    )
                stats.sent += 1
        else:
            # More than the cap qualified: one digest beats N notifications.
            body = "\n\n".join(c.line() for c in passing)
            subject = f"✈ {len(passing)} fares worth a look (cheapest {fmt_money(passing[0].price_cents)})"
            if dry_run:
                print(f"  [dry-run digest] {subject}\n{body}")
            else:
                notify(cfg.notify, subject, body, passing[0].deep_link)
            stats.sent = 1
            stats.digested = len(passing)
    except NotifyError as exc:
        # Delivery failure must not be recorded as a sent alert, or the dedup
        # window would suppress the retry on the next run.
        stats.delivery_error = str(exc)
        if verbose:
            print(f"[alert] DELIVERY FAILED: {exc}")
        return stats

    if not dry_run:
        _record(conn, passing)
    return stats
