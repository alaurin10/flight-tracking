"""The watchdog. Silent failure is the worst outcome this system can have.

If cron dies, the IP gets blocked, or Google changes the page, the visible
symptom is *no alerts* — which is indistinguishable from *no deals*. This
module turns "nothing has worked for a while" into a notification, so the
absence of news is never mistaken for news.

Everything here is a read of tables the other modules already write:
`run_log`, `fetch_attempts`, `queries.last_fetch_at`, `state`, `feed_log`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import Config
from .db import get_state, parse_ts, set_state, utcnow
from .fetch import PRIORITY_INTERVAL_DAYS, STATE_BLOCKED_STREAK, STATE_LAST_SUCCESS, cooldown_active
from .notify import NotifyError, notify

STATE_LAST_HEALTH_ALERT = "last_health_alert_at"
STATE_LAST_HEALTH_SIG = "last_health_signature"


@dataclass
class Problem:
    code: str
    message: str
    severity: str = "crit"   # 'crit' | 'warn'


@dataclass
class HealthReport:
    checked_at: str
    problems: list[Problem] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(p.severity == "crit" for p in self.problems)

    @property
    def signature(self) -> str:
        return ",".join(sorted(p.code for p in self.problems))

    def render(self) -> str:
        out = [f"health @ {self.checked_at}: " + ("OK" if self.ok else "PROBLEMS")]
        for p in self.problems:
            out.append(f"  [{p.severity.upper():4}] {p.code}: {p.message}")
        for i in self.info:
            out.append(f"  [info] {i}")
        return "\n".join(out)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_since(ts: str | None, now: datetime) -> float | None:
    dt = parse_ts(ts)
    return (now - dt).total_seconds() / 3600.0 if dt else None


def check(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> HealthReport:
    now = now or datetime.now(timezone.utc)
    rep = HealthReport(checked_at=_iso(now))

    n_active = conn.execute(
        "SELECT COUNT(*) FROM queries q JOIN routes r ON r.id = q.route_id WHERE q.active = 1 AND r.active = 1"
    ).fetchone()[0]
    n_runs = conn.execute("SELECT COUNT(*) FROM run_log WHERE kind IN ('fetch', 'confirm')").fetchone()[0]
    rep.info.append(f"{n_active} active queries, {n_runs} fetch runs recorded")
    if n_active == 0:
        rep.problems.append(Problem("no_queries", "no active queries — run `flighttrack expand`", "warn"))

    # --- is anything working? ---------------------------------------------
    last_ok = get_state(conn, STATE_LAST_SUCCESS)
    if last_ok is None:
        row = conn.execute(
            "SELECT MAX(observed_at) AS t FROM observations WHERE is_best = 1"
        ).fetchone()
        last_ok = row["t"] if row else None
    age = _hours_since(last_ok, now)
    if age is None:
        if n_runs:
            rep.problems.append(Problem("never_succeeded", f"{n_runs} run(s) recorded but no observation has ever been written — run `flighttrack doctor`"))
        else:
            rep.info.append("no runs yet — nothing to judge")
    else:
        rep.info.append(f"last successful fetch {age:.1f}h ago")
        if age > cfg.health.max_hours_without_success:
            rep.problems.append(Problem(
                "stale_success",
                f"no successful fetch in {age:.0f}h (limit {cfg.health.max_hours_without_success}h)",
            ))

    # --- is cron firing? ---------------------------------------------------
    last_run = conn.execute("SELECT started_at, finished_at FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
    if last_run:
        run_age = _hours_since(last_run["started_at"], now)
        if run_age is not None and run_age > 48 and n_runs > 1:
            rep.problems.append(Problem("no_recent_run", f"last run started {run_age:.0f}h ago — has cron stopped?"))
    unfinished = conn.execute(
        "SELECT COUNT(*) FROM run_log WHERE finished_at IS NULL AND started_at < ?",
        (_iso(now - timedelta(hours=2)),),
    ).fetchone()[0]
    if unfinished:
        rep.problems.append(Problem("unfinished_runs", f"{unfinished} run(s) never finished — process killed mid-run?", "warn"))

    # --- blocking ------------------------------------------------------------
    until = cooldown_active(conn, now)
    streak = int(get_state(conn, STATE_BLOCKED_STREAK, "0") or 0)
    if until:
        rep.problems.append(Problem("cooldown", f"in cooldown until {until} after {streak} blocked run(s)", "warn" if streak < 2 else "crit"))
    elif streak >= 2:
        rep.problems.append(Problem("blocked_streak", f"{streak} consecutive runs were blocked", "warn"))

    # --- last 24h of attempts, by outcome ----------------------------------
    since = _iso(now - timedelta(hours=24))
    counts = {r["outcome"]: r["n"] for r in conn.execute(
        "SELECT outcome, COUNT(*) AS n FROM fetch_attempts WHERE at >= ? GROUP BY outcome", (since,)
    )}
    if counts:
        rep.info.append("last 24h attempts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        total = sum(counts.values())
        good = counts.get("ok", 0) + counts.get("empty", 0)
        if good == 0 and total >= 2:
            worst = max((k for k in counts if k not in ("ok", "empty")), key=lambda k: counts[k], default="unknown")
            rep.problems.append(Problem("all_failing", f"all {total} attempts in the last 24h failed (mostly {worst})"))
        if counts.get("layout", 0) >= 3:
            rep.problems.append(Problem(
                "layout", f"{counts['layout']} layout failures in 24h — Google's page may have changed; "
                          f"see {cfg.failure_dir} and `flighttrack doctor`",
                "crit" if good == 0 else "warn",
            ))

    # --- coverage: how much of the grid is stale? ---------------------------
    if n_active:
        stale = 0
        for row in conn.execute(
            "SELECT q.last_fetch_at, r.priority FROM queries q JOIN routes r ON r.id = q.route_id "
            "WHERE q.active = 1 AND r.active = 1"
        ):
            allowed = 2 * PRIORITY_INTERVAL_DAYS.get(row["priority"], 3) * 24 + 24
            h = _hours_since(row["last_fetch_at"], now)
            if h is None:
                stale += 1 if n_runs >= 7 else 0   # a brand-new grid is not stale, just new
            elif h > allowed:
                stale += 1
        frac = stale / n_active
        rep.info.append(f"stale queries: {stale}/{n_active} ({frac:.0%})")
        if frac > cfg.health.stale_fraction_warn:
            rep.problems.append(Problem("coverage", f"{frac:.0%} of queries are overdue — raise the cap, narrow the horizon, or enable the calendar sweep", "warn"))

    # --- deal feeds ------------------------------------------------------------
    if cfg.deals.enabled:
        for feed in cfg.deals.feeds:
            last_good = conn.execute(
                "SELECT MAX(at) AS t FROM feed_log WHERE feed = ? AND ok = 1", (feed,)
            ).fetchone()["t"]
            last_any = conn.execute("SELECT MAX(at) AS t FROM feed_log WHERE feed = ?", (feed,)).fetchone()["t"]
            if last_any is None:
                continue
            h = _hours_since(last_good, now)
            if h is None or h > 48:
                rep.problems.append(Problem("feed_" + feed.split("//")[-1].split("/")[0], f"feed {feed} has not succeeded in {'ever' if h is None else f'{h:.0f}h'}", "warn"))

    return rep


def run(conn: sqlite3.Connection, cfg: Config, dry_run: bool = False, verbose: bool = True,
        now: datetime | None = None) -> tuple[HealthReport, bool]:
    """Check, print, and notify once per problem-set per `renotify_hours`."""
    now = now or datetime.now(timezone.utc)
    rep = check(conn, cfg, now)
    if verbose:
        print(rep.render())

    if rep.ok and not rep.problems:
        # Fully clean: forget the last alert so a recurrence notifies promptly.
        set_state(conn, STATE_LAST_HEALTH_SIG, None)
        return rep, False
    if rep.ok:
        return rep, False   # warnings only: visible in `status`, not worth a push

    if not cfg.health.notify or (cfg.notify or {}).get("channel", "none") == "none":
        return rep, False

    last = _hours_since(get_state(conn, STATE_LAST_HEALTH_ALERT), now)
    sig_changed = get_state(conn, STATE_LAST_HEALTH_SIG) != rep.signature
    if last is not None and last < cfg.health.renotify_hours and not sig_changed:
        return rep, False

    subject = "⚠ flighttrack is not collecting prices"
    body = "\n".join(f"- {p.message}" for p in rep.problems if p.severity == "crit")
    body += "\n\nRun `flighttrack status` and `flighttrack doctor` on the host."
    if dry_run:
        if verbose:
            print(f"  [dry-run] would notify: {subject}\n{body}")
        return rep, True
    try:
        notify(cfg.notify, subject, body)
    except NotifyError as exc:
        if verbose:
            print(f"[health] notification failed: {exc}")
        return rep, False
    set_state(conn, STATE_LAST_HEALTH_ALERT, utcnow())
    set_state(conn, STATE_LAST_HEALTH_SIG, rep.signature)
    return rep, True
