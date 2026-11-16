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

from flighttrack import fetch, jobs, pace
from flighttrack.config import Calendar, Collect, Deals, Fetch, Trip
from flighttrack.notifier import Notifier
from flighttrack.scheduler import (
    STATE_LAST_SWEEP,
    STATE_NEXT_BATCH,
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


def slot_now(conn, clock):
    """Make the next slot due now, without moving the clock."""
    set_state(conn, STATE_NEXT_BATCH, iso(clock.t))


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


def test_a_slot_sends_batch_size_requests_then_waits_the_even_gap(tmp_path, clock):
    """At 720 a day a slot of three is followed by 3 × 120 s = 6 minutes, ±40%."""
    w, conn, src = build(tmp_path, collect=Collect(daily_request_budget=720, batch_size=3))
    w.start()
    go_to_next_batch(conn, clock)
    ran_at = clock.t
    assert w.tick() == "batch"
    assert len(src.calls) == 3
    gap = (next_batch(conn) - ran_at).total_seconds()
    assert 0.6 * 360 <= gap <= 1.4 * 360
    clock.advance(minutes=3)
    assert w.tick() == "waiting" and len(src.calls) == 3


def test_the_gap_follows_the_pace_and_never_goes_under_the_floor(tmp_path, clock):
    w, conn, _ = build(tmp_path, collect=Collect(daily_request_budget=50, batch_size=5))
    assert pace.gap_seconds(conn, w.cfg) == 5 * 86400 / 50
    fast = Collect(daily_request_budget=100_000, batch_size=1, min_gap_seconds=60)
    w2, conn2, _ = build(tmp_path / "b", collect=fast)
    w2.start()
    go_to_next_batch(conn2, clock)
    w2.tick()
    assert next_batch(conn2) - clock.t >= timedelta(seconds=60)


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
    clock.advance(minutes=30)
    slot_now(conn, clock)
    w.tick()
    assert len(src.calls) == 7  # 5, then the 2 the budget had left
    clock.advance(minutes=30)
    slot_now(conn, clock)
    assert w.tick() == "budget"
    assert fetch.budget_frees_at(conn, w.cfg, clock.t) == attempts(conn)[0] + timedelta(hours=24)


def test_seven_days_of_downtime_trips_first_and_the_budget_is_never_exceeded(tmp_path, clock):
    """Acceptance: after seven simulated days of downtime trips are fetched first, and
    no 24-hour window ever holds more requests than the budget."""
    budget = 20
    collect = Collect(daily_request_budget=budget, batch_size=5)
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
    # The allowance is spread over the day, and nearly all of it is used.
    last_day = len([t for t in times if t > clock.t - timedelta(hours=24)])
    assert budget - collect.batch_size <= last_day <= budget


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
# Lanes: detail pages and calendar windows share the pace
# ---------------------------------------------------------------------------


def _calendar(rows):
    inner = json.dumps([None, [[d, r, [[None, p]], 1] for d, r, p in rows]])
    body = ")]}'\n\n1\n" + json.dumps([["wrb.fr", None, inner, None, None, None, "generic"]])
    return body + "\n" + " " * 1200  # a real reply is kilobytes; see calendar.looks_gated


GATED = ')]}\'\n\n[["wrb.fr","GetCalendarGraph",null,null,null,[13],"generic"]]'


class Always:
    """A calendar transport that answers every call the same way."""

    name = "fake"

    def __init__(self, status, text):
        self.status, self.text, self.calls = status, text, []

    def post(self, url, data, params=None, headers=None, timeout=30.0):
        from flighttrack.gflights.transport import Response

        self.calls.append(data)
        return Response(self.status, url, self.text, 0.01, "fake", {})

    get = post


def _all_pairs(conn):
    rows = conn.execute("SELECT depart_date, return_date FROM queries WHERE active = 1")
    return [(r["depart_date"], r["return_date"], 500 + i) for i, r in enumerate(rows)]


def calendar_under_its_share(conn, clock):
    """Recent use that leaves the calendar lane furthest under its share."""
    conn.execute("DELETE FROM fetch_attempts")
    conn.executemany(
        "INSERT INTO fetch_attempts (at, source, outcome) VALUES (?, 'google_html', 'ok')",
        [(iso(clock.t),)] * 10,
    )
    conn.commit()


def run_for(worker, clock, hours, step_minutes=1):
    end = clock.t + timedelta(hours=hours)
    while clock.t < end:
        worker.tick()
        clock.advance(minutes=step_minutes)


def test_lanes_converge_on_their_shares_when_both_have_work(tmp_path, clock):
    """Detail 60 and calendar 25 (explore has nothing to do yet, so its 15 is shared):
    calendar should get 25/85 of the requests over a day."""
    collect = Collect(daily_request_budget=720, batch_size=3, pair_refresh_hours=1)
    cal = Calendar(enabled=True, refresh_hours=0.25, flags_per_window=0)
    w, conn, src = build(tmp_path, collect=collect, cfg_over={"calendar": cal})
    w.transport = Always(200, _calendar(_all_pairs(conn)))
    w.start()
    run_for(w, clock, 24)
    used = pace.lane_used(conn, clock.t)
    total = used["detail"] + used["calendar"]
    assert total >= 600  # the day's allowance, nearly all of it
    assert 0.24 <= used["calendar"] / total <= 0.35
    assert used["detail"] == len(src.calls)


def test_a_lane_with_nothing_due_leaves_its_share_to_the_other(tmp_path, clock):
    cal = Calendar(enabled=True, refresh_hours=1000)  # each window once, then never due
    collect = Collect(daily_request_budget=720, batch_size=3, pair_refresh_hours=1)
    w, conn, src = build(tmp_path, collect=collect, cfg_over={"calendar": cal})
    w.transport = Always(200, _calendar(_all_pairs(conn)))
    w.start()
    run_for(w, clock, 12)
    windows = conn.execute("SELECT COUNT(*) FROM scans WHERE kind = 'calendar'").fetchone()[0]
    assert len(w.transport.calls) == windows  # every window once
    assert len(src.calls) >= 330  # detail took the rest of half a day's 720


def test_calendar_windows_flag_their_best_dates_for_the_next_detail_batch(tmp_path, clock):
    cal = Calendar(enabled=True, flags_per_window=2)
    w, conn, _src = build(tmp_path, cfg_over={"calendar": cal})
    w.transport = Always(200, _calendar(_all_pairs(conn)))
    w.start()
    # Every pair fetched just now, so only flags can make one due before the rotation.
    conn.execute("UPDATE queries SET last_fetch_at = ?", (iso(clock.t - timedelta(hours=7)),))
    conn.execute(
        "INSERT INTO fetch_attempts (at, source, outcome) VALUES (?, 'google_html', 'ok')",
        (iso(clock.t),),
    )
    conn.commit()
    slot_now(conn, clock)
    assert w.tick() == "calendar"
    flagged = [q for q, _ in fetch.flagged(conn, clock.t)]
    assert flagged and len(flagged) <= 2 * len(w.transport.calls)
    assert get_state(conn, STATE_LAST_SWEEP) == iso(clock.t)
    conn.execute("UPDATE queries SET last_fetch_at = ?", (iso(clock.t),))
    conn.commit()
    clock.advance(minutes=1)
    due = fetch.select_due(conn, w.cfg, now=clock.t, limit=99)
    assert [d.query_id for d in due] == []  # fetched after the flag: satisfied
    conn.execute("UPDATE queries SET last_fetch_at = ?", (iso(clock.t - timedelta(hours=2)),))
    conn.commit()
    due = fetch.select_due(conn, w.cfg, now=clock.t, limit=99)
    assert [d.query_id for d in due][: len(flagged)] == flagged


def test_a_gated_calendar_reply_pauses_only_its_lane(tmp_path, clock):
    cal = Calendar(enabled=True)
    w, conn, _src = build(tmp_path, cfg_over={"calendar": cal})
    w.transport = Always(200, GATED)
    w.start()
    conn.execute(
        "INSERT INTO fetch_attempts (at, source, outcome) VALUES (?, 'google_html', 'ok')",
        (iso(clock.t),),
    )
    conn.commit()
    slot_now(conn, clock)
    assert w.tick() == "calendar"
    assert len(w.transport.calls) == 1  # stopped at the first declined window
    outcome = conn.execute("SELECT outcome FROM fetch_attempts ORDER BY id DESC").fetchone()[0]
    assert outcome == "gated"
    assert fetch.cooldown_active(conn, clock.t) is None  # not a block
    assert pace.lane_paused(conn, "calendar", clock.t) == iso(clock.t + timedelta(minutes=30))
    # The window was not priced, so it stays due.
    assert conn.execute("SELECT last_run_at FROM scans").fetchone()[0] is None
    # Detail goes on meanwhile.
    for _ in range(3):
        slot_now(conn, clock)
        assert w.tick() == "batch"
    assert len(w.transport.calls) == 1
    # It escalates on the next declined reply, and an answer clears it.
    clock.advance(minutes=31)
    slot_now(conn, clock)
    calendar_under_its_share(conn, clock)
    assert w.tick() == "calendar"
    assert pace.lane_paused(conn, "calendar", clock.t) == iso(clock.t + timedelta(hours=2))
    clock.advance(hours=3)
    w.transport = Always(200, _calendar(_all_pairs(conn)))
    calendar_under_its_share(conn, clock)
    slot_now(conn, clock)
    assert w.tick() == "calendar"
    assert pace.lane_pause(conn, "calendar") is None


def test_a_blocked_calendar_window_stays_due_after_the_cooldown(tmp_path, clock):
    w, conn, _ = build(tmp_path, cfg_over={"calendar": Calendar(enabled=True)})
    w.transport = Always(429, "slow down")
    w.start()
    conn.execute(
        "INSERT INTO fetch_attempts (at, source, outcome) VALUES (?, 'google_html', 'ok')",
        (iso(clock.t),),
    )
    conn.commit()
    slot_now(conn, clock)
    assert w.tick() == "calendar"
    until = parse_ts(fetch.cooldown_active(conn, clock.t))
    assert until is not None and next_batch(conn) >= until + timedelta(minutes=5)
    row = conn.execute("SELECT last_run_at, last_outcome FROM scans").fetchone()
    assert row["last_run_at"] is None and row["last_outcome"] == "blocked"


def test_no_calendar_slot_when_the_calendar_is_off(tmp_path, clock):
    w, conn, _ = build(tmp_path)
    w.transport = FakeTransport([])
    w.start()
    for _ in range(5):
        slot_now(conn, clock)
        assert w.tick() == "batch"
    assert w.transport.calls == []


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
        "pace",
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
