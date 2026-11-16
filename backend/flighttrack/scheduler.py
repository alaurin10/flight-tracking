"""The worker: one long-running process that collects on its own, recovers on its own,
and says so when it cannot (APScheduler, local time).

What to fetch next is worked out from the database, not from the clock: a date pair
is due when its last fetch is older than its refresh interval (`fetch.select_due`).
After a block, a restart or a week of downtime the worker looks at the same tables
and carries on. There is no "today's run" to miss.

One lock means a slot, a canary request, a queued job and the housekeeping never
overlap. All Google traffic shares one pacing slot, `next_batch_at` in `state`. Each slot
sends up to `collect.batch_size` requests and goes to the lane furthest under its share of
the last 24 hours (`collect.shares`): detail pages for date pairs, calendar windows of
the flexible searches older than `calendar.refresh_hours`, or Explore departures of the
region boards older than `explore.refresh_hours` (`explore.py`). The gap after a slot
spreads the day's allowance evenly: requests sent × 24 h ÷ the allowance, jittered ±40%,
never under `collect.min_gap_seconds`. The allowance is learned (`pace.py`): it rises after
clean busy days and falls on a block. A block also starts the escalating cooldown, and the
next slot is the end of the cooldown plus 5 to 20 minutes. Google declining a calendar or
Explore call without data pauses only that lane.

| Job        | When                                   | Does                                       |
|------------|----------------------------------------|--------------------------------------------|
| tick       | every 20 s                             | the next slot, when it is due              |
| pace       | hourly                                 | `pace.review`: raise the pace if earned    |
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
from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import alert as alert_mod
from . import canary as canary_mod
from . import deals as deals_mod
from . import explore as explore_mod
from . import fetch as fetch_mod
from . import health as health_mod
from . import jobs, pace, watches
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
STATE_LAST_SWEEP = "last_sweep_at"  # when a calendar window last answered with prices
STATE_LAST_BACKUP = "last_backup_at"
STATE_OLD_SWEEP_PLAN = "sweep_plan"  # the once-a-day plan before D1; cleared at start

START_DELAY_MINUTES = (1.0, 5.0)
IDLE_MINUTES = 10.0  # nothing due in any lane: look again after this
JITTER = (0.6, 1.4)
AFTER_BLOCK_MINUTES = (5.0, 20.0)
CANARY_EVERY_HOURS = 24


def _no_sleep(_seconds: float) -> None:
    return None


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
        # The fake source has nobody to be polite to: no pauses between its requests.
        self.sleeper = _no_sleep if self.fake and sleeper is time.sleep else sleeper
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
            set_state(conn, STATE_OLD_SWEEP_PLAN, None)
            pace.review(conn, self.cfg, now)
            log.info(
                "worker started%s; first slot at %s; pace %d requests a day",
                " (fake source: no outside requests)" if self.fake else "",
                iso(first),
                pace.allowance(conn, self.cfg),
            )
        finally:
            conn.close()

    # -- the pacing slot --------------------------------------------------------

    def tick(self) -> str:
        """Every 20 s: run the next slot if it is due. Returns what it did."""
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
        lane = self.choose_lane(conn, now)
        if lane is None:
            set_state(conn, STATE_NEXT_BATCH, iso(now + timedelta(minutes=IDLE_MINUTES)))
            return "idle"
        if lane == "calendar":
            self._calendar_slot(conn, now, remaining)
            return "calendar"
        if lane == "explore":
            self._explore_slot(conn, now, remaining)
            return "explore"
        self._batch(conn, now, remaining)
        return "batch"

    # -- lanes ------------------------------------------------------------------

    def _detail_due(self, conn: sqlite3.Connection, now: datetime) -> bool:
        return bool(fetch_mod.select_due(conn, self.cfg, now=now, limit=1))

    def _calendar_due(self, conn: sqlite3.Connection, now: datetime) -> bool:
        if not self.cfg.calendar.enabled or self.fake:
            return False
        if pace.lane_paused(conn, "calendar", now):
            return False
        return bool(sweep_mod.due_windows(conn, self.cfg, now))

    def _explore_due(self, conn: sqlite3.Connection, now: datetime) -> bool:
        if not self.cfg.explore.enabled or self.fake:
            return False
        if pace.lane_paused(conn, "explore", now):
            return False
        return bool(explore_mod.due(conn, self.cfg, now))

    def choose_lane(self, conn: sqlite3.Connection, now: datetime) -> str | None:
        """The lane this slot goes to: dates a person asked for first; otherwise, among
        the lanes with work due, the one furthest under its share of the last 24 hours.
        A lane with nothing due leaves its share to the others."""
        if fetch_mod.prune_requested(conn, now, save=False) and self._detail_due(conn, now):
            return "detail"
        ready = [
            lane
            for lane, due in (
                ("detail", self._detail_due(conn, now)),
                ("calendar", self._calendar_due(conn, now)),
                ("explore", self._explore_due(conn, now)),
            )
            if due
        ]
        if not ready:
            return None
        if len(ready) == 1:
            return ready[0]
        shares = pace.lane_shares(self.cfg, tuple(ready))
        used = pace.lane_used(conn, now)
        return min(ready, key=lambda lane: (used[lane] / shares[lane], lane != "detail"))

    # -- the pacing slot --------------------------------------------------------

    def _push_past_cooldown(self, conn: sqlite3.Connection, until: str) -> None:
        """During a cooldown the next slot is its end plus 5–20 minutes."""
        end = parse_ts(until)
        nxt = parse_ts(get_state(conn, STATE_NEXT_BATCH))
        if end is not None and (nxt is None or nxt < end):
            set_state(conn, STATE_NEXT_BATCH, iso(end + self._minutes(AFTER_BLOCK_MINUTES)))

    def _schedule_next(self, conn: sqlite3.Connection, sent: int = 0) -> datetime:
        """After a slot that sent `sent` requests: the even spacing for them at the
        current pace, jittered, never under `collect.min_gap_seconds`."""
        now = self.now()
        until = parse_ts(fetch_mod.cooldown_active(conn, now))
        if until is not None:
            at = until + self._minutes(AFTER_BLOCK_MINUTES)
        else:
            base = pace.gap_seconds(conn, self.cfg, max(1, sent))
            gap = max(self.cfg.collect.min_gap_seconds, base * self.rng.uniform(*JITTER))
            at = now + timedelta(seconds=gap)
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
        at = self._schedule_next(conn, stats.attempted + stats.retries)
        log.info("next slot at %s", iso(at))
        return stats

    def _alerts(self, conn: sqlite3.Connection) -> None:
        try:
            s = alert_mod.run(conn, self.cfg, verbose=False, notifier=self.notifier)
            if s.sent or s.delivery_error:
                log.info("alerts: %s", s.summary())
        except Exception:
            log.exception("alerts failed")

    def _calendar_slot(
        self, conn: sqlite3.Connection, now: datetime, remaining: int
    ) -> sweep_mod.SweepStats:
        """The stalest calendar windows, up to a slot's worth. Each window's best dates are
        flagged for the next detail batches; a reply without data pauses this lane."""
        group = sweep_mod.due_windows(conn, self.cfg, now)[
            : min(self.cfg.collect.batch_size, remaining)
        ]
        stats = sweep_mod.run(
            conn,
            self.cfg,
            transport=self.transport,
            now=now,
            sleeper=self.sleeper,
            verbose=True,
            jobs=group,
        )
        log.info("calendar: %s", stats.summary())
        if stats.flags:
            fetch_mod.flag_from_sweep(conn, stats.flags, now)
        if stats.ok:
            set_state(conn, STATE_LAST_SWEEP, iso(now))
        if stats.gated:
            until = pace.pause_lane(conn, "calendar", "Google answered without prices", now)
            log.warning("calendar lane paused until %s (Google declined without data)", until)
        elif stats.ok:
            pace.clear_lane(conn, "calendar")
        conn.commit()
        self._schedule_next(conn, stats.calls)
        return stats

    def _explore_slot(
        self, conn: sqlite3.Connection, now: datetime, remaining: int
    ) -> explore_mod.ExploreStats:
        """The stalest Explore departures, up to a slot's worth. A reply without data
        pauses this lane; a block starts the shared cooldown."""
        group = explore_mod.due(conn, self.cfg, now)[: min(self.cfg.collect.batch_size, remaining)]
        stats = explore_mod.run(
            conn, self.cfg, group, transport=self.transport, now=now, sleeper=self.sleeper
        )
        log.info("explore: %s", stats.summary())
        if stats.gated:
            until = pace.pause_lane(conn, "explore", "Google answered without places", now)
            log.warning("explore lane paused until %s (Google declined without data)", until)
        elif stats.ok:
            pace.clear_lane(conn, "explore")
        conn.commit()
        self._schedule_next(conn, stats.calls)
        return stats

    def review_pace(self) -> str:
        """Hourly: let the pace rise when the last day earned it."""
        result: str = self._locked(
            "pace", lambda c: pace.review(c, self.cfg, self.now()), wait=True
        )
        if result == "raised":
            log.info("pace raised")
        return result

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
                f"today's request allowance ({pace.allowance(conn, self.cfg)} in 24 hours) "
                f"is spent; these dates go first at {iso(frees or now)}"
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
    """Local time (the container's TZ) for the nightly jobs."""
    sched = BlockingScheduler()
    opts: dict[str, Any] = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 1800}
    start = datetime.now().astimezone()

    def soon(minutes: float) -> datetime:
        return start + timedelta(minutes=minutes)

    sched.add_job(
        worker.heartbeat, IntervalTrigger(seconds=30), id="heartbeat", next_run_time=start, **opts
    )
    sched.add_job(
        worker.tick, IntervalTrigger(seconds=20), id="tick", next_run_time=soon(0.1), **opts
    )
    sched.add_job(worker.process_jobs, IntervalTrigger(seconds=15), id="jobs", **opts)
    sched.add_job(
        worker.review_pace, IntervalTrigger(hours=1), id="pace", next_run_time=soon(5), **opts
    )
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
