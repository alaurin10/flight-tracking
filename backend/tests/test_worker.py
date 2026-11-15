"""The worker (M4), driven by hand: an injected clock, FakeSource, and no sleeping.

Each scheduled job is a `Worker` method, so these tests call `tick()` (every minute),
`process_jobs()` (every 15 s) and friends directly and move the clock in between.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

import pytest
from helpers import FakeClock, FakeTransport, make_config, make_db

from flighttrack import fetch, jobs
from flighttrack.config import Calendar, Collect, Deals, Fetch, Trip
from flighttrack.notifier import Notifier
from flighttrack.scheduler import (
    STATE_LAST_SWEEP,
    STATE_NEXT_BATCH,
    STATE_SWEEP_PLAN,
    Worker,
    build_scheduler,
)
from flighttrack.settings import Settings
from flighttrack.source import FakeSource
from flighttrack.store.db import get_state, iso, parse_ts, set_clock, set_state

START = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
TODAY = START.date()
TRIP_DEPART = (TODAY + timedelta(days=40)).isoformat()
TRIP_RETURN = (TODAY + timedelta(days=54)).isoformat()
TRIP_KEY = ("HND", TRIP_DEPART, TRIP_RETURN)


@pytest.fixture
def clock():
    c = FakeClock(START)
    set_clock(c)
    return c


def build(tmp_path, *, trips=(), source=None, collect=None, cfg_over=None, settings_over=None):
    """A database loaded with the test watches, and a worker on it."""
    collect = collect or Collect(daily_request_budget=50, batch_size=5)
    cfg = make_config(
        trips=list(trips),
        collect=collect,
        failure_dir=str(tmp_path / "failures"),
        fetch=Fetch(max_queries_per_run=5, min_sleep_seconds=0, max_sleep_seconds=0),
        **(cfg_over or {}),
    )
    conn, cfg = make_db(tmp_path, cfg, today=TODAY)
    settings = Settings(
        data_dir=tmp_path / "data", db_path=tmp_path / "t.db", **(settings_over or {})
    )
    src = source if source is not None else FakeSource()
    worker = Worker(
        settings,
        cfg,
        source_factory=lambda c: src,
        sleeper=lambda s: None,
        rng=random.Random(7),
        notifier=Notifier(),
    )
    return worker, conn, src


def next_batch(conn):
    return parse_ts(get_state(conn, STATE_NEXT_BATCH))


def go_to_next_batch(conn, clock):
    clock.set(max(clock.t, next_batch(conn)))


def attempts(conn):
    return [
        parse_ts(r[0])
        for r in conn.execute("SELECT at FROM fetch_attempts WHERE source != 'serpapi' ORDER BY at")
    ]


# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------


def test_start_schedules_the_first_batch_one_to_five_minutes_out(tmp_path, clock):
    w, conn, _ = build(tmp_path)
    w.start()
    first = next_batch(conn)
    assert START + timedelta(minutes=1) <= first <= START + timedelta(minutes=5)
    assert w.tick() == "waiting"


def test_a_worker_killed_mid_run_marks_the_run_failed_at_the_next_start(tmp_path, clock):
    """Acceptance: the process dies during a batch (here: KeyboardInterrupt from the
    source, which nothing catches), leaving its run open; the next start closes it."""

    class Dies(FakeSource):
        def fetch(self, cfg, route, depart, ret, limit):
            if len(self.calls) == 1:
                self.calls.append((route.dest, depart, ret))
                raise KeyboardInterrupt
            return super().fetch(cfg, route, depart, ret, limit)

    w, conn, _ = build(tmp_path, source=Dies())
    jobs.enqueue(conn, "run_batch")
    conn.execute("UPDATE jobs SET status = 'running'")  # claimed when the process died
    conn.commit()
    w.start()
    go_to_next_batch(conn, clock)
    with pytest.raises(KeyboardInterrupt):
        w.tick()
    run = conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
    assert run["finished_at"] is None

    clock.advance(minutes=10)
    w2, *_ = build(tmp_path)  # a new process on the same database
    w2.start()
    run = conn.execute("SELECT * FROM run_log WHERE id = ?", (run["id"],)).fetchone()
    assert run["finished_at"] == iso(clock.t) and run["failed"] >= 1
    assert "interrupted" in run["notes"]
    job = conn.execute("SELECT status, error FROM jobs").fetchone()
    assert job["status"] == "failed" and "interrupted" in job["error"]


# ---------------------------------------------------------------------------
# Batches, blocks and the cooldown
# ---------------------------------------------------------------------------


def test_a_batch_fetches_batch_size_pairs_then_waits_a_gap(tmp_path, clock):
    w, conn, src = build(tmp_path, collect=Collect(batch_size=3, batch_gap_minutes=(25, 45)))
    w.start()
    go_to_next_batch(conn, clock)
    ran_at = clock.t
    assert w.tick() == "batch"
    assert len(src.calls) == 3
    assert ran_at + timedelta(minutes=25) <= next_batch(conn) <= ran_at + timedelta(minutes=45)
    clock.advance(minutes=20)
    assert w.tick() == "waiting" and len(src.calls) == 3


def test_a_block_mid_batch_sets_the_cooldown_and_the_next_batch_comes_after_it(tmp_path, clock):
    """Acceptance: a block mid-batch sets the cooldown and the next batch is scheduled
    after it; when the cooldown passes, the remaining pairs are fetched."""
    w, conn, src = build(tmp_path)
    w.start()
    go_to_next_batch(conn, clock)
    due = fetch.select_due(conn, w.cfg, now=clock.t, limit=5)
    keys = [(d.route.dest, d.depart_date, d.return_date) for d in due]
    src.prices[keys[2]] = "blocked"

    blocked_at = clock.t
    assert w.tick() == "batch"
    assert src.calls == keys[:3]  # stopped at the block
    until = parse_ts(fetch.cooldown_active(conn, clock.t))
    assert until == blocked_at + timedelta(hours=1)  # first step of cooldown_hours
    nb = next_batch(conn)
    assert until + timedelta(minutes=5) <= nb <= until + timedelta(minutes=20)

    # Nothing goes out during the cooldown, however often the tick fires.
    while clock.t < until:
        assert w.tick() == "cooldown"
        clock.advance(minutes=1)
    assert len(src.calls) == 3

    # After it: the blocked pair and the rest of the batch.
    del src.prices[keys[2]]
    go_to_next_batch(conn, clock)
    assert w.tick() == "batch"
    assert src.calls[3:6] == keys[2:5]
    assert fetch.cooldown_active(conn, clock.t) is None
    assert get_state(conn, fetch.STATE_BLOCKED_STREAK) == "0"


def test_a_cooldown_started_elsewhere_moves_the_next_batch_past_it(tmp_path, clock):
    w, conn, src = build(tmp_path)
    w.start()
    until = parse_ts(fetch.enter_cooldown(conn, w.cfg, clock.t))  # e.g. by the canary
    assert w.tick() == "cooldown"
    nb = next_batch(conn)
    assert until + timedelta(minutes=5) <= nb <= until + timedelta(minutes=20)
    assert src.calls == []


def test_the_budget_stops_a_batch_and_waits(tmp_path, clock):
    w, conn, src = build(tmp_path, collect=Collect(daily_request_budget=7, batch_size=5))
    w.start()
    go_to_next_batch(conn, clock)
    w.tick()
    go_to_next_batch(conn, clock)
    w.tick()
    assert len(src.calls) == 7  # 5, then the 2 the budget had left
    go_to_next_batch(conn, clock)
    assert w.tick() == "budget"
    assert fetch.budget_frees_at(conn, w.cfg, clock.t) == attempts(conn)[0] + timedelta(hours=24)


def test_seven_days_of_downtime_trips_first_and_the_budget_is_never_exceeded(tmp_path, clock):
    """Acceptance: after seven simulated days of downtime trips are fetched first, and
    no 24-hour window ever holds more requests than the budget."""
    budget = 20
    collect = Collect(daily_request_budget=budget, batch_size=5, batch_gap_minutes=(25, 45))
    trip = Trip("japan", "HND", TRIP_DEPART, TRIP_RETURN, target_price=80000)
    w, conn, src = build(tmp_path, trips=[trip], collect=collect)

    def run_for(worker, hours):
        end = clock.t + timedelta(hours=hours)
        while clock.t < end:
            worker.tick()
            worker.process_jobs()
            clock.advance(minutes=1)

    w.start()
    run_for(w, 30)
    assert src.calls[0] == TRIP_KEY  # never fetched: first from the start

    clock.advance(days=7)  # the box was off for a week
    src.calls.clear()
    w2, *_ = build(tmp_path, trips=[trip], source=src, collect=collect)
    w2.start()
    run_for(w2, 72)

    assert src.calls[0] == TRIP_KEY
    assert src.calls.count(TRIP_KEY) >= 3  # refreshed every trip_refresh_hours
    times = attempts(conn)
    for i, t in enumerate(times):
        window = [u for u in times[: i + 1] if u > t - timedelta(hours=24)]
        assert len(window) <= budget, f"{len(window)} requests in the 24 h before {t}"
    # The budget is what limited it, not a lack of due pairs.
    assert len([t for t in times if t > clock.t - timedelta(hours=24)]) == budget


# ---------------------------------------------------------------------------
# The job queue
# ---------------------------------------------------------------------------


def test_a_job_for_a_trip_is_fetched_within_one_tick(tmp_path, clock):
    """Acceptance: a job for a trip is fetched within one tick of the queue."""
    trip = Trip("japan", "HND", TRIP_DEPART, TRIP_RETURN, target_price=80000)
    w, conn, src = build(tmp_path, trips=[trip])
    w.start()
    # The trip was fetched an hour ago, so nothing would pick it on its own.
    go_to_next_batch(conn, clock)
    w.tick()
    assert src.calls[0] == TRIP_KEY
    clock.advance(minutes=3)
    src.calls.clear()

    job_id = jobs.enqueue(conn, "fetch_trip", {"trip": "japan"}, requested_by="owner")
    assert w.process_jobs() == 1
    assert src.calls[0] == TRIP_KEY
    job = jobs.get(conn, job_id)
    assert job["status"] == "done" and job["error"] is None
    assert fetch.requested(conn) == []  # satisfied, so no longer first in line


def test_a_job_during_a_cooldown_says_when_and_goes_first_after_it(tmp_path, clock):
    trip = Trip("japan", "HND", TRIP_DEPART, TRIP_RETURN)
    w, conn, src = build(tmp_path, trips=[trip])
    w.start()
    go_to_next_batch(conn, clock)
    w.tick()
    clock.advance(minutes=1)
    until = fetch.enter_cooldown(conn, w.cfg, clock.t)
    src.calls.clear()

    job_id = jobs.enqueue(conn, "fetch_trip", {"trip": "japan"})
    w.process_jobs()
    job = jobs.get(conn, job_id)
    assert job["status"] == "failed" and f"paused until {until}" in job["error"]
    assert src.calls == []

    clock.set(parse_ts(until))
    go_to_next_batch(conn, clock)
    assert w.tick() == "batch"
    assert src.calls[0] == TRIP_KEY


def test_fetch_search_and_unknown_jobs(tmp_path, clock):
    w, conn, src = build(tmp_path)
    w.start()
    slug = conn.execute("SELECT slug FROM searches ORDER BY id LIMIT 1").fetchone()[0]
    ok = jobs.enqueue(conn, "fetch_search", {"search": slug})
    missing = jobs.enqueue(conn, "fetch_trip", {"trip": "nowhere"})
    conn.execute(
        "INSERT INTO jobs (kind, payload, created_at) VALUES ('teleport', '{}', ?)", (iso(clock.t),)
    )
    conn.commit()
    assert w.process_jobs() == 3
    assert jobs.get(conn, ok)["status"] == "done"
    assert len(src.calls) == w.cfg.collect.batch_size
    assert jobs.get(conn, missing)["status"] == "failed"
    assert (
        "unknown job kind 'teleport'"
        in conn.execute("SELECT error FROM jobs WHERE kind = 'teleport'").fetchone()[0]
    )


def test_jobs_queue_basics(tmp_path, clock):
    _, conn, _ = build(tmp_path)
    with pytest.raises(ValueError, match="unknown job kind"):
        jobs.enqueue(conn, "nap")
    a = jobs.enqueue(conn, "poll_deals")
    b = jobs.enqueue(conn, "run_batch", {"why": "test"})
    assert jobs.pending(conn) == 2
    first = jobs.claim_next(conn)
    assert first["id"] == a and first["status"] == "running" and first["payload"] == {}
    jobs.finish(conn, a)
    second = jobs.claim_next(conn)
    assert second["id"] == b and second["payload"] == {"why": "test"}
    jobs.finish(conn, b, error="nope")
    assert jobs.claim_next(conn) is None
    assert [jobs.get(conn, i)["status"] for i in (a, b)] == ["done", "failed"]


# ---------------------------------------------------------------------------
# The calendar sweep in groups
# ---------------------------------------------------------------------------


def _calendar(rows):
    inner = json.dumps([None, [[d, r, [[None, p]], 1] for d, r, p in rows]])
    return ")]}'\n\n1\n" + json.dumps([["wrb.fr", None, inner, None, None, None, "generic"]]) + "\n"


def test_the_sweep_runs_in_groups_of_four_then_flags_dates_for_the_next_batch(tmp_path, clock):
    def cal_cfg():
        return {"calendar": Calendar(enabled=True, max_calls_per_run=12, confirm_budget=2)}

    w, conn, src = build(tmp_path, cfg_over=cal_cfg())
    pairs = conn.execute("SELECT id, depart_date, return_date FROM queries WHERE active = 1")
    rows = [(p["depart_date"], p["return_date"], 500 + i) for i, p in enumerate(pairs)]
    # Every window is answered with all the pairs; each prices what it matches.
    w.transport = FakeTransport([(200, None, _calendar(rows))] * 20)
    w.start()  # no sweep has ever finished: one is planned at start
    plan = json.loads(get_state(conn, STATE_SWEEP_PLAN))
    n = len(plan["keys"])
    assert 4 < n <= 12

    groups = 0
    while get_state(conn, STATE_SWEEP_PLAN):
        go_to_next_batch(conn, clock)
        before = len(w.transport.calls)
        assert w.tick() == "sweep"
        assert len(w.transport.calls) - before <= 4
        groups += 1
    assert groups == -(-n // 4) and len(w.transport.calls) == n
    assert src.calls == []  # no detail fetch while sweeping
    assert get_state(conn, STATE_LAST_SWEEP) == iso(clock.t)

    flagged = json.loads(get_state(conn, fetch.STATE_SWEEP_FLAGGED))["ids"]
    assert len(flagged) == 2
    go_to_next_batch(conn, clock)
    assert w.tick() == "batch"
    first_two = {
        conn.execute("SELECT depart_date FROM queries WHERE id = ?", (q,)).fetchone()[0]
        for q in flagged
    }
    assert {c[1] for c in src.calls[:2]} == first_two


def test_a_blocked_sweep_group_retries_that_window_after_the_cooldown(tmp_path, clock):
    cfg_over = {"calendar": Calendar(enabled=True, max_calls_per_run=6)}
    w, conn, _ = build(tmp_path, cfg_over=cfg_over)
    w.transport = FakeTransport([(200, None, _calendar([])), (429, None, "slow down")])
    w.start()
    go_to_next_batch(conn, clock)
    assert w.tick() == "sweep"
    plan = json.loads(get_state(conn, STATE_SWEEP_PLAN))
    assert plan["next"] == 1  # the first window done; the blocked one is tried again
    until = parse_ts(fetch.cooldown_active(conn, clock.t))
    assert until is not None and next_batch(conn) >= until + timedelta(minutes=5)


def test_no_sweep_when_the_calendar_is_off(tmp_path, clock):
    w, conn, _ = build(tmp_path)
    w.start()
    assert w.sweep() == "disabled" and get_state(conn, STATE_SWEEP_PLAN) is None


# ---------------------------------------------------------------------------
# The rest of the schedule
# ---------------------------------------------------------------------------


def test_backup_and_retention_jobs(tmp_path, clock):
    w, conn, _ = build(tmp_path)
    w.start()
    w.backup()
    assert [p.name for p in (tmp_path / "data" / "backups").iterdir()] != []
    old = iso(clock.t - timedelta(days=200))
    conn.execute(
        "INSERT INTO notifications (kind, title, body, created_at) VALUES ('health','t','b',?)",
        (old,),
    )
    conn.commit()
    w.retention()
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0
    w.vacuum()


def test_heartbeat_and_scheduler_jobs(tmp_path, clock):
    w, _, _ = build(tmp_path)
    w.heartbeat()
    assert w.settings.heartbeat_path.exists()
    ids = {j.id for j in build_scheduler(w).get_jobs()}
    assert ids == {
        "heartbeat",
        "tick",
        "jobs",
        "sweep",
        "deals",
        "health",
        "canary",
        "backup",
        "retention",
        "vacuum",
    }


def test_health_job_records_a_notification_when_nothing_works(tmp_path, clock):
    w, conn, _ = build(tmp_path)
    set_state(conn, fetch.STATE_LAST_SUCCESS, iso(clock.t - timedelta(days=3)))
    w.health()
    row = conn.execute("SELECT kind, title FROM notifications").fetchone()
    assert row["kind"] == "health" and "stopped collecting" in row["title"]


def test_alerts_follow_a_batch(tmp_path, clock):
    w, conn, src = build(tmp_path)
    src.default = [9000]  # under every target in the test config
    w.start()
    go_to_next_batch(conn, clock)
    w.tick()
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE kind = 'price'").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] >= 1


def test_fake_source_mode_collects_at_once_and_makes_no_outside_requests(tmp_path, clock):
    """FLIGHTTRACK_SOURCE=fake (the compose smoke test): the first batch goes within
    seconds, prices are stored, and the feeds, sweep and canary are skipped."""
    cfg_over = {"calendar": Calendar(enabled=True), "deals": Deals(enabled=True, feeds=("f",))}
    w, conn, _ = build(tmp_path, cfg_over=cfg_over, settings_over={"flighttrack_source": "fake"})
    w._source_factory = Worker(w.settings, w.cfg)._source_factory  # the real default
    w.transport = FakeTransport([])  # any outside request would fail the test
    w.start()
    assert next_batch(conn) <= START + timedelta(seconds=5)
    assert get_state(conn, STATE_SWEEP_PLAN) is None
    clock.advance(seconds=5)
    assert w.tick() == "batch"
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE source = 'fake'").fetchone()[0]
    assert w.canary() == "skipped"
    w.deals()
    assert w.transport.calls == []


def test_fake_source_mode_does_not_pause_between_requests(tmp_path):
    """With nobody to be polite to, the fake worker's batches don't sleep 4–8 s per
    request (so the Manage e2e sees a first price within seconds). The real source,
    and any sleeper a test passes, are left alone."""
    import time as time_mod

    fake = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "t.db", flighttrack_source="fake")
    real = Settings(data_dir=tmp_path / "d", db_path=tmp_path / "t.db")
    cfg = make_config()
    w = Worker(fake, cfg, notifier=Notifier())
    assert w.sleeper is not time_mod.sleep
    started = time_mod.monotonic()
    w.sleeper(6.0)
    assert time_mod.monotonic() - started < 1
    assert Worker(real, cfg, notifier=Notifier()).sleeper is time_mod.sleep
    mine = lambda s: None  # noqa: E731
    assert Worker(fake, cfg, sleeper=mine, notifier=Notifier()).sleeper is mine
