"""The worker: one long-running process that collects on its own, recovers on its own,
and says so when it cannot (APScheduler, local time).

What to fetch next is worked out from the database, not from the clock: a date pair
is due when its last fetch is older than its refresh interval (`fetch.select_due`).
After a block, a restart or a week of downtime the worker looks at the same tables
and carries on. There is no "today's run" to miss.

One lock means a detail batch, a sweep group, a canary request, a queued job and the
housekeeping never overlap. All Google traffic shares one pacing slot,
`next_batch_at` in `state`: each slot is either a detail batch (`collect.batch_size`
requests) or a group of four calendar calls, followed by a random gap
(`collect.batch_gap_minutes`). Every request counts against one rolling 24-hour
budget (`collect.daily_request_budget`), counted from `fetch_attempts`. A block
starts the escalating cooldown, and the next slot is the end of the cooldown plus
5 to 20 minutes.

| Job        | When                                   | Does                                       |
|------------|----------------------------------------|--------------------------------------------|
| tick       | every minute                           | the next slot, when it is due              |
| sweep      | daily at `collect.sweep_at`, and at    | plans the day's calendar windows; the      |
|            | start if none finished in 26 h         | slots then run them four at a time         |
| deals      | every 3 h                              | `deals.run`                                |
| health     | hourly                                 | `health.run`, notifying when it breaks and |
|            |                                        | when it recovers                           |
| canary     | hourly check, one request a day        | `canary.run`                               |
| jobs       | every 15 s                             | the queue (`jobs.py`)                      |
| backup     | nightly 03:30                          | `store.backup`, 14 kept                    |
| retention  | nightly 03:45                          | `store.retention.prune`                    |
| vacuum     | weekly, Sunday 04:40                   | `VACUUM`                                   |
| heartbeat  | every 30 s                             | touches `data/worker.heartbeat`            |
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import alert as alert_mod
from . import canary as canary_mod
from . import deals as deals_mod
from . import fetch as fetch_mod
from . import health as health_mod
from . import jobs, watches
from . import sweep as sweep_mod
from .config import Config
from .expand import expand
from .gflights.transport import Transport
from .notifier import Notifier, NotifyError
from .settings import Settings
from .source import Source, make_source
from .store import retention
from .store.backup import backup, vacuum
from .store.db import connect, get_state, iso, migrate, parse_ts, set_state
from .store.db import now as clock_now

log = logging.getLogger("flighttrack.worker")

STATE_NEXT_BATCH = "next_batch_at"
# The day's calendar windows while they are being worked through:
# {"started_at", "day", "keys": [...], "next": i, "candidates": [[ratio, cents, qid, why]]}
STATE_SWEEP_PLAN = "sweep_plan"
STATE_LAST_SWEEP = "last_sweep_at"  # when the last full sweep finished
STATE_LAST_BACKUP = "last_backup_at"

SWEEP_GROUP = 4
SWEEP_CATCHUP_HOURS = 26
START_DELAY_MINUTES = (1.0, 5.0)
AFTER_BLOCK_MINUTES = (5.0, 20.0)
CANARY_EVERY_HOURS = 24


class Worker:
    """Holds the lock and the pacing rules. Every scheduled job is a method, so tests
    call them directly with an injected clock (`store.db.set_clock`) and no sleeping."""

    def __init__(
        self,
        settings: Settings,
        cfg: Config,
        *,
        source_factory: Callable[[Config], Source] | None = None,
        transport: Transport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.settings = settings
        self.cfg = cfg
        self.fake = settings.fake_source
        self.lock = threading.Lock()
        self.sleeper = sleeper
        self.rng = rng or random.Random()
        self.transport = transport  # calendar, canary and feeds; None = the default
        self._source_factory = source_factory or (lambda c: make_source(c, fake=self.fake))
        self._source: Source | None = None
        if notifier is None:
            try:
                notifier = Notifier.from_config(cfg)
            except NotifyError as exc:
                log.error("notifications are recorded but not delivered: %s", exc)
                notifier = Notifier()
        self.notifier = notifier

    # -- plumbing -------------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        return connect(self.settings.database)

    def now(self) -> datetime:
        return clock_now()

    def source(self) -> Source:
        if self._source is None:
            self._source = self._source_factory(self.cfg)
        return self._source

    def _minutes(self, span: tuple[float, float]) -> timedelta:
        return timedelta(minutes=self.rng.uniform(*span))

    def _locked(self, name: str, fn: Callable[[sqlite3.Connection], Any], *, wait: bool) -> Any:
        """Run `fn` under the lock with its own connection. Never raises: a job that
        fails is logged and the scheduler goes on."""
        if not self.lock.acquire(blocking=wait):
            return "busy"
        try:
            conn = self.connect()
            try:
                return fn(conn)
            finally:
                conn.close()
        except Exception:
            log.exception("%s failed", name)
            return "error"
        finally:
            self.lock.release()

    # -- start-up ---------------------------------------------------------------

    def start(self) -> None:
        """Migrate, repair what a crash left running, expand, schedule the first slot."""
        conn = self.connect()
        try:
            applied = migrate(conn)
            if applied:
                log.info("applied database migrations: %s", ", ".join(applied))
            runs, failed_jobs = retention.recover_interrupted(conn)
            if runs or failed_jobs:
                log.warning("marked %d interrupted runs and %d jobs as failed", runs, failed_jobs)
            now = self.now()
            try:
                log.info("expand: %s", expand(conn, self.cfg, today=now.date()).summary())
            except Exception:
                log.exception("expand failed at start")
            # The fake source has nobody to be polite to: its first batch goes at once.
            delay = timedelta(seconds=5) if self.fake else self._minutes(START_DELAY_MINUTES)
            first = now + delay
            until = parse_ts(fetch_mod.cooldown_active(conn, now))
            if until is not None:
                first = max(first, until + self._minutes(AFTER_BLOCK_MINUTES))
            set_state(conn, STATE_NEXT_BATCH, iso(first))
            log.info(
                "worker started%s; first batch at %s",
                " (fake source: no outside requests)" if self.fake else "",
                iso(first),
            )
            if self._sweep_overdue(conn, now):
                self._begin_sweep(conn, now)
        finally:
            conn.close()

    # -- the pacing slot --------------------------------------------------------

    def tick(self) -> str:
        """Every minute: run the next slot if it is due. Returns what it did."""
        result: str = self._locked("tick", self._tick, wait=False)
        return result

    def _tick(self, conn: sqlite3.Connection) -> str:
        now = self.now()
        until = fetch_mod.cooldown_active(conn, now)
        if until:
            self._push_past_cooldown(conn, until)
            return "cooldown"
        nxt = parse_ts(get_state(conn, STATE_NEXT_BATCH))
        if nxt is not None and now < nxt:
            return "waiting"
        remaining = fetch_mod.budget_remaining(conn, self.cfg, now)
        if remaining <= 0:
            return "budget"
        if self._sweep_plan(conn):
            self._sweep_group(conn, now, remaining)
            return "sweep"
        self._batch(conn, now, remaining)
        return "batch"

    def _push_past_cooldown(self, conn: sqlite3.Connection, until: str) -> None:
        """During a cooldown the next slot is its end plus 5–20 minutes."""
        end = parse_ts(until)
        nxt = parse_ts(get_state(conn, STATE_NEXT_BATCH))
        if end is not None and (nxt is None or nxt < end):
            set_state(conn, STATE_NEXT_BATCH, iso(end + self._minutes(AFTER_BLOCK_MINUTES)))

    def _schedule_next(self, conn: sqlite3.Connection) -> datetime:
        now = self.now()
        until = parse_ts(fetch_mod.cooldown_active(conn, now))
        if until is not None:
            at = until + self._minutes(AFTER_BLOCK_MINUTES)
        else:
            at = now + self._minutes(self.cfg.collect.batch_gap_minutes)
        set_state(conn, STATE_NEXT_BATCH, iso(at))
        return at

    def _batch(
        self, conn: sqlite3.Connection, now: datetime, remaining: int
    ) -> fetch_mod.FetchStats:
        """One detail batch, then alerts, then the next slot."""
        stats = fetch_mod.run(
            conn,
            self.cfg,
            self.source(),
            now=now,
            sleeper=self.sleeper,
            limit=min(self.cfg.collect.batch_size, remaining),
            max_requests=remaining,
        )
        log.info("batch: %s", stats.summary())
        fetch_mod.prune_requested(conn)
        self._alerts(conn)
        at = self._schedule_next(conn)
        log.info("next batch at %s", iso(at))
        return stats

    def _alerts(self, conn: sqlite3.Connection) -> None:
        try:
            s = alert_mod.run(conn, self.cfg, verbose=False, notifier=self.notifier)
            if s.sent or s.delivery_error:
                log.info("alerts: %s", s.summary())
        except Exception:
            log.exception("alerts failed")

    # -- the calendar sweep -----------------------------------------------------

    def sweep(self) -> str:
        """Daily: plan today's calendar windows. The slots run them four at a time."""
        result: str = self._locked("sweep", lambda c: self._begin_sweep(c, self.now()), wait=True)
        return result

    def _sweep_overdue(self, conn: sqlite3.Connection, now: datetime) -> bool:
        if not self.cfg.calendar.enabled or self.fake or self._sweep_plan(conn):
            return False
        last = parse_ts(get_state(conn, STATE_LAST_SWEEP))
        return last is None or now - last > timedelta(hours=SWEEP_CATCHUP_HOURS)

    def _sweep_plan(self, conn: sqlite3.Connection) -> dict[str, Any] | None:
        try:
            plan = json.loads(get_state(conn, STATE_SWEEP_PLAN) or "null")
        except ValueError:
            return None
        return plan if isinstance(plan, dict) and plan.get("keys") else None

    def _begin_sweep(self, conn: sqlite3.Connection, now: datetime) -> str:
        if not self.cfg.calendar.enabled:
            return "disabled"
        if self.fake:
            log.info("sweep skipped: fake source")
            return "skipped"
        old = self._sweep_plan(conn)
        if old:
            self._finish_sweep(conn, old, now)  # yesterday's did not finish: keep its picks
        today = now.date()
        windows = sweep_mod._rotate(
            sweep_mod.plan(conn, self.cfg, today), conn, self.cfg.calendar.max_calls_per_run
        )
        if not windows:
            set_state(conn, STATE_LAST_SWEEP, iso(now))
            return "nothing to sweep"
        plan = {
            "started_at": iso(now),
            "day": today.isoformat(),
            "keys": [w.key() for w in windows],
            "next": 0,
            "candidates": [],
        }
        set_state(conn, STATE_SWEEP_PLAN, json.dumps(plan))
        log.info("sweep planned: %d calendar windows", len(windows))
        return "planned"

    def _sweep_group(self, conn: sqlite3.Connection, now: datetime, remaining: int) -> None:
        plan = self._sweep_plan(conn)
        assert plan is not None
        started = parse_ts(plan.get("started_at"))
        if started is None or now - started > timedelta(hours=SWEEP_CATCHUP_HOURS):
            self._finish_sweep(conn, plan, now)  # too old: its windows have moved on
            self._batch(conn, now, remaining)
            return
        keys: list[str] = plan["keys"][plan["next"] : plan["next"] + min(SWEEP_GROUP, remaining)]
        current = {
            w.key(): w for w in sweep_mod.plan(conn, self.cfg, date.fromisoformat(plan["day"]))
        }
        group = [current[k] for k in keys if k in current]
        advance = len(keys)
        if group:
            stats = sweep_mod.run(
                conn,
                self.cfg,
                transport=self.transport,
                now=now,
                sleeper=self.sleeper,
                verbose=True,
                jobs=group,
            )
            log.info("sweep group: %s", stats.summary())
            plan["candidates"] += [list(c) for c in stats.candidates]
            if stats.blocked and stats.calls:
                # Try the blocked window again once the cooldown is over.
                advance = keys.index(group[stats.calls - 1].key())
        plan["next"] += advance
        if plan["next"] >= len(plan["keys"]):
            self._finish_sweep(conn, plan, now)
        else:
            set_state(conn, STATE_SWEEP_PLAN, json.dumps(plan))
        self._schedule_next(conn)

    def _finish_sweep(self, conn: sqlite3.Connection, plan: dict[str, Any], now: datetime) -> None:
        """Flag the most promising dates; the next batches confirm them."""
        picked: list[int] = []
        for _ratio, _cents, qid, _why in sorted(plan.get("candidates") or []):
            if qid not in picked:
                picked.append(int(qid))
            if len(picked) >= self.cfg.calendar.confirm_budget:
                break
        fetch_mod.flag_from_sweep(conn, picked, now)
        set_state(conn, STATE_SWEEP_PLAN, None)
        set_state(conn, STATE_LAST_SWEEP, iso(now))
        log.info("sweep finished: %d dates flagged for a detail fetch", len(picked))

    # -- the job queue ----------------------------------------------------------

    def process_jobs(self) -> int:
        """Every 15 s: run queued jobs. Returns how many finished."""
        done: int = self._locked("jobs", self._process_jobs, wait=False)
        return done if isinstance(done, int) else 0

    def _process_jobs(self, conn: sqlite3.Connection) -> int:
        n = 0
        while (job := jobs.claim_next(conn)) is not None:
            error: str | None
            try:
                error = self._run_job(conn, job)
            except Exception as exc:
                log.exception("job %s failed", job["id"])
                error = f"{type(exc).__name__}: {exc}"
            jobs.finish(conn, job["id"], error=error)
            log.info("job %d %s: %s", job["id"], job["kind"], error or "done")
            n += 1
        return n

    def _run_job(self, conn: sqlite3.Connection, job: dict[str, Any]) -> str | None:
        kind, payload = job["kind"], job["payload"]
        today = self.now().date().isoformat()
        if kind == "fetch_trip":
            trip = watches.get_trip(conn, payload.get("trip"))
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT q.id FROM watch_queries wq JOIN queries q ON q.id = wq.query_id "
                    "WHERE wq.trip_id = ? AND q.active = 1 AND q.depart_date >= ? "
                    "ORDER BY q.depart_date",
                    (trip.id, today),
                )
            ]
            if not ids:
                return f"trip {trip.slug!r} has no dates to fetch (is it active and ahead?)"
            fetch_mod.request_queries(conn, ids, self.now())
            return self._batch_now(conn)
        if kind == "fetch_search":
            search = watches.get_search(conn, payload.get("search"))
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT q.id FROM watch_queries wq JOIN queries q ON q.id = wq.query_id "
                    "WHERE wq.search_id = ? AND q.active = 1 AND q.depart_date >= ? "
                    "ORDER BY q.last_fetch_at IS NOT NULL, q.last_fetch_at, q.depart_date "
                    "LIMIT ?",
                    (search.id, today, self.cfg.collect.batch_size),
                )
            ]
            if not ids:
                return f"search {search.slug!r} has no dates to fetch (is it active?)"
            fetch_mod.request_queries(conn, ids, self.now())
            return self._batch_now(conn)
        if kind == "poll_deals":
            self._deals(conn)
            return None
        if kind == "run_batch":
            return self._batch_now(conn)
        return f"unknown job kind {kind!r}"

    def _batch_now(self, conn: sqlite3.Connection) -> str | None:
        """A batch now, with the requested dates first, when the cooldown and the budget
        allow. Otherwise say when collecting resumes (the dates stay first in line)."""
        now = self.now()
        until = fetch_mod.cooldown_active(conn, now)
        if until:
            return (
                f"collecting is paused until {until} (Google asked us to slow down); "
                "these dates go first when it resumes"
            )
        remaining = fetch_mod.budget_remaining(conn, self.cfg, now)
        if remaining <= 0:
            frees = fetch_mod.budget_frees_at(conn, self.cfg, now)
            return (
                f"the daily request budget ({self.cfg.collect.daily_request_budget}) is "
                f"spent; these dates go first at {iso(frees or now)}"
            )
        stats = self._batch(conn, now, remaining)
        if stats.blocked:
            return f"stopped: {stats.abort_reason}; " + "; ".join(stats.errors[-1:])
        if stats.attempted and not stats.succeeded and not stats.empty:
            return "; ".join(stats.errors)[:500] or "every request failed"
        return None

    # -- everything else ----------------------------------------------------------

    def deals(self) -> None:
        self._locked("deals", self._deals, wait=True)

    def _deals(self, conn: sqlite3.Connection) -> None:
        if not self.cfg.deals.enabled:
            return
        if self.fake:
            log.info("deal feeds skipped: fake source")
            return
        deals_mod.run(
            conn,
            self.cfg,
            transport=self.transport,
            now=self.now(),
            sleeper=self.sleeper,
            notifier=self.notifier,
        )

    def health(self) -> None:
        self._locked(
            "health",
            lambda c: health_mod.run(c, self.cfg, now=self.now(), notifier=self.notifier),
            wait=True,
        )

    def canary(self) -> str:
        """Hourly check; one request when the last one is a day old and the cooldown
        and the budget allow."""
        result: str = self._locked("canary", self._canary, wait=True)
        return result

    def _canary(self, conn: sqlite3.Connection) -> str:
        if self.fake:
            return "skipped"
        now = self.now()
        try:
            last = json.loads(get_state(conn, canary_mod.STATE_LAST_CANARY) or "{}")
        except ValueError:
            last = {}
        at = parse_ts(last.get("at") if isinstance(last, dict) else None)
        if at is not None and now - at < timedelta(hours=CANARY_EVERY_HOURS):
            return "not due"
        if fetch_mod.cooldown_active(conn, now):
            return "cooldown"
        if fetch_mod.budget_remaining(conn, self.cfg, now) <= 0:
            return "budget"
        res = canary_mod.run(conn, self.cfg, transport=self.transport, now=now)
        if fetch_mod.cooldown_active(conn, self.now()):
            self._schedule_next(conn)
        return res.result

    def backup(self) -> None:
        def run(conn: sqlite3.Connection) -> None:
            now = self.now()
            path = backup(conn, self.cfg, self.settings.backup_dir, today=now.astimezone().date())
            set_state(conn, STATE_LAST_BACKUP, iso(now))
            log.info("backup written to %s", path)

        self._locked("backup", run, wait=True)

    def retention(self) -> None:
        self._locked(
            "retention",
            lambda c: log.info("retention: %s", retention.prune(c, self.cfg, now=self.now())),
            wait=True,
        )

    def vacuum(self) -> None:
        self._locked("vacuum", vacuum, wait=True)

    def heartbeat(self) -> None:
        """Show the scheduler is alive (separate from the lock, so a long batch counts)."""
        path = self.settings.heartbeat_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def _hm(text: str) -> tuple[int, int]:
    h, m = text.split(":")
    return int(h), int(m)


def build_scheduler(worker: Worker) -> BlockingScheduler:
    """Local time (the container's TZ): `collect.sweep_at` and the nightly jobs."""
    sched = BlockingScheduler()
    opts: dict[str, Any] = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 1800}
    start = datetime.now().astimezone()

    def soon(minutes: float) -> datetime:
        return start + timedelta(minutes=minutes)

    sched.add_job(
        worker.heartbeat, IntervalTrigger(seconds=30), id="heartbeat", next_run_time=start, **opts
    )
    sched.add_job(
        worker.tick, IntervalTrigger(minutes=1), id="tick", next_run_time=soon(0.1), **opts
    )
    sched.add_job(worker.process_jobs, IntervalTrigger(seconds=15), id="jobs", **opts)
    h, m = _hm(worker.cfg.collect.sweep_at)
    sched.add_job(worker.sweep, CronTrigger(hour=h, minute=m), id="sweep", **opts)
    sched.add_job(worker.deals, IntervalTrigger(hours=3), id="deals", next_run_time=soon(2), **opts)
    sched.add_job(
        worker.health, IntervalTrigger(hours=1), id="health", next_run_time=soon(10), **opts
    )
    sched.add_job(
        worker.canary, IntervalTrigger(hours=1), id="canary", next_run_time=soon(20), **opts
    )
    sched.add_job(worker.backup, CronTrigger(hour=3, minute=30), id="backup", **opts)
    sched.add_job(worker.retention, CronTrigger(hour=3, minute=45), id="retention", **opts)
    sched.add_job(
        worker.vacuum, CronTrigger(day_of_week="sun", hour=4, minute=40), id="vacuum", **opts
    )
    return sched
