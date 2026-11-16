"""The learned pace (pace.py): how many Google requests a day, raised after clean busy
days, cut on a block, capped below the volumes that drew blocks. Offline, with an
injected clock and requests written straight into `fetch_attempts`."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from helpers import FakeClock, make_config, make_db

from flighttrack import fetch, pace
from flighttrack.config import Calendar, Collect, Fetch
from flighttrack.notifier import Notifier
from flighttrack.scheduler import STATE_NEXT_BATCH, Worker
from flighttrack.settings import Settings
from flighttrack.source import FakeSource
from flighttrack.store.db import get_state, iso, parse_ts, set_clock

START = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def clock():
    c = FakeClock(START)
    set_clock(c)
    return c


def db(tmp_path, collect=None, **over):
    cfg = make_config(collect=collect or Collect(), **over)
    return make_db(tmp_path, cfg, today=START.date())


def send(conn, clock, n, hours=24.0, source="google_html", outcome="ok"):
    """`n` requests spread over the `hours` before now."""
    step = hours * 3600 / max(n, 1)
    conn.executemany(
        "INSERT INTO fetch_attempts (at, source, outcome) VALUES (?, ?, ?)",
        [(iso(clock.t - timedelta(seconds=i * step)), source, outcome) for i in range(n)],
    )
    conn.commit()


def busy_day(conn, cfg, clock):
    """A day that used the whole allowance, then the review at its end."""
    clock.advance(hours=24)
    send(conn, clock, pace.allowance(conn, cfg))
    return pace.review(conn, cfg, clock.t)


def test_the_pace_starts_at_the_start_and_climbs_to_the_ceiling_over_clean_busy_days(
    tmp_path, clock
):
    conn, cfg = db(tmp_path)
    assert pace.allowance(conn, cfg) == 50
    assert pace.review(conn, cfg, clock.t) == "started"
    seen = [50]
    for _ in range(10):
        busy_day(conn, cfg, clock)
        seen.append(pace.allowance(conn, cfg))
    assert seen[:8] == [50, 75, 113, 170, 255, 383, 575, 720]
    assert seen[-1] == 720  # never above the ceiling
    assert pace.load(conn, cfg).last_review == "at its ceiling"


def test_a_quiet_day_proves_nothing(tmp_path, clock):
    conn, cfg = db(tmp_path)
    pace.review(conn, cfg, clock.t)
    clock.advance(hours=24)
    send(conn, clock, 30)  # 60% of 50
    assert pace.review(conn, cfg, clock.t).startswith("quiet")
    assert pace.allowance(conn, cfg) == 50


def test_it_waits_a_full_day_at_each_pace(tmp_path, clock):
    conn, cfg = db(tmp_path)
    pace.review(conn, cfg, clock.t)
    clock.advance(hours=23)
    send(conn, clock, 50)
    assert pace.review(conn, cfg, clock.t) == "waiting for 24 hours at this pace"
    clock.advance(hours=1)
    assert pace.review(conn, cfg, clock.t) == "raised"


def test_a_block_cuts_to_60_percent_holds_and_never_passes_85_percent_for_30_days(tmp_path, clock):
    conn, cfg = db(tmp_path, collect=Collect(pace_start_per_day=300))
    pace.review(conn, cfg, clock.t)
    send(conn, clock, 300)
    assert pace.on_block(conn, cfg, clock.t) == 180  # 60% of the 300 sent
    p = pace.load(conn, cfg)
    assert p.hold_until == iso(clock.t + timedelta(days=3)) and p.blocks[-1]["sent"] == 300
    assert pace.block_cap(p, clock.t) == 255  # 85% of 300

    # Busy days during the hold change nothing.
    for _ in range(2):
        assert busy_day(conn, cfg, clock) == "holding after a block"
    assert pace.allowance(conn, cfg) == 180

    # Then 10% a day, up to the cap, and no further while the block is remembered.
    allowances = []
    for _ in range(25):
        busy_day(conn, cfg, clock)
        allowances.append(pace.allowance(conn, cfg))
    assert allowances[:2] == [198, 218]  # 180 + 10%, then 10% more
    assert max(allowances) == 255
    assert pace.load(conn, cfg).last_review == "at the cap below a block"

    # 30 days on, the block is forgotten and the normal step returns.
    while clock.t < START + timedelta(days=31):
        busy_day(conn, cfg, clock)
    assert pace.allowance(conn, cfg) > 255


def test_a_block_never_cuts_below_one_request_an_hour(tmp_path, clock):
    conn, cfg = db(tmp_path)
    send(conn, clock, 3)
    assert pace.on_block(conn, cfg, clock.t) == pace.MIN_PER_DAY


def test_a_fixed_budget_is_never_changed(tmp_path, clock):
    conn, cfg = db(tmp_path, collect=Collect(daily_request_budget=80))
    assert pace.allowance(conn, cfg) == 80
    assert busy_day(conn, cfg, clock) == "fixed"
    send(conn, clock, 80)
    assert pace.on_block(conn, cfg, clock.t) == 80
    assert pace.load(conn, cfg).fixed and pace.next_review_at(pace.load(conn, cfg)) is None


def test_a_lowered_ceiling_applies_at_once(tmp_path, clock):
    conn, cfg = db(tmp_path)
    pace.review(conn, cfg, clock.t)
    for _ in range(5):
        busy_day(conn, cfg, clock)
    assert pace.allowance(conn, cfg) == 383
    lower = make_config(collect=Collect(pace_max_per_day=200))
    assert pace.allowance(conn, lower) == 200


def test_the_budget_reads_the_pace(tmp_path, clock):
    conn, cfg = db(tmp_path)
    send(conn, clock, 45)
    assert fetch.budget_remaining(conn, cfg, clock.t) == 5
    send(conn, clock, 5, hours=1)
    assert fetch.budget_remaining(conn, cfg, clock.t) == 0
    assert fetch.budget_frees_at(conn, cfg, clock.t) is not None


def test_lane_use_and_shares(tmp_path, clock):
    conn, cfg = db(tmp_path, calendar=Calendar(enabled=True))
    send(conn, clock, 6, source="google_html")
    send(conn, clock, 2, source="fast_flights")
    send(conn, clock, 3, source="google_calendar")
    send(conn, clock, 1, source="canary")
    send(conn, clock, 4, source="serpapi")
    assert pace.lane_used(conn, clock.t) == {"detail": 8, "calendar": 3, "explore": 0}
    assert pace.requests_sent(conn, clock.t) == 12  # the canary counts; SerpApi does not
    shares = pace.lane_shares(cfg)
    assert set(shares) == {"detail", "calendar"}  # explore is not built yet: its 15 is shared
    assert shares["detail"] == pytest.approx(60 / 85)
    off = make_config(calendar=Calendar(enabled=False))
    assert pace.lane_shares(off) == {"detail": 1.0}


def test_lane_pauses_escalate_and_clear(tmp_path, clock):
    conn, _ = db(tmp_path)
    steps = []
    for _ in range(5):
        until = pace.pause_lane(conn, "calendar", "declined", clock.t)
        steps.append(parse_ts(until) - clock.t)
    assert (
        steps
        == [timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6)]
        + [timedelta(hours=24)] * 2
    )
    assert pace.lane_pause(conn, "calendar")["since"] == iso(clock.t)
    assert pace.lane_paused(conn, "calendar", clock.t + timedelta(hours=25)) is None
    pace.clear_lane(conn, "calendar")
    assert pace.lane_pause(conn, "calendar") is None


def test_fourteen_days_with_two_blocks_never_exceed_the_allowance_in_force(tmp_path, clock):
    """The worker on a learned pace for two weeks, blocked on days 4 and 9: before every
    request, the 24 hours behind it hold fewer requests than the allowance at that
    moment. The pace climbs, falls on each block, and climbs again."""
    collect = Collect(pace_start_per_day=100, batch_size=3, pair_refresh_hours=1)
    cfg = make_config(
        collect=collect,
        failure_dir=str(tmp_path / "failures"),
        fetch=Fetch(max_queries_per_run=5, min_sleep_seconds=0, max_sleep_seconds=0),
    )
    conn, cfg = make_db(tmp_path, cfg, today=START.date())
    blocks = [START + timedelta(days=4), START + timedelta(days=9)]
    over: list[str] = []
    seen: list[int] = []

    class Watched(FakeSource):
        def fetch(self, cfg_, route, depart, ret, limit):
            now = clock.t
            sent, allowed = pace.requests_sent(conn, now), pace.allowance(conn, cfg)
            if sent >= allowed:
                over.append(f"{iso(now)}: {sent} sent, allowance {allowed}")
            if blocks and now >= blocks[0]:
                blocks.pop(0)
                self.calls.append((route.dest, depart, ret))
                from flighttrack.source import BlockedError

                raise BlockedError("synthetic block")
            return super().fetch(cfg_, route, depart, ret, limit)

    src = Watched()
    w = Worker(
        Settings(data_dir=tmp_path / "data", db_path=tmp_path / "t.db"),
        cfg,
        source_factory=lambda c: src,
        sleeper=lambda s: None,
        rng=random.Random(3),
        notifier=Notifier(),
    )
    w.start()
    minute = 0
    while clock.t < START + timedelta(days=14):
        if minute % 60 == 0:
            w.review_pace()
            seen.append(pace.allowance(conn, cfg))
        if parse_ts(get_state(conn, STATE_NEXT_BATCH)) <= clock.t:
            w.tick()
        clock.advance(minutes=2)
        minute += 2
    assert over == []
    assert blocks == []  # both blocks happened
    day = 24
    assert max(seen[: 4 * day]) > 100  # it climbed before the first block
    assert min(seen[4 * day : 5 * day]) < max(seen[: 4 * day])  # and fell on it
    assert len(pace.load(conn, cfg).blocks) == 2
