"""Retention: the price series survives, everything else ages out, triggers come back."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import sqlite3

from flighttrack import compact, demo, jobs
from flighttrack.config import Retention
from flighttrack.db import connect, get_state
from flighttrack.source import FakeSource
from helpers import make_config


def _seeded(tmp_path, **ret):
    cfg = replace(make_config(max_days_ahead=60, notify={"channel": "none"}), retention=Retention(**ret))
    conn = connect(tmp_path / "c.db")
    demo.seed(conn, cfg, days=120)
    return conn, cfg


def test_compact_prunes_only_runner_ups_and_logs(tmp_path):
    conn, cfg = _seeded(tmp_path, runner_up_days=30, attempts_days=10)
    best_before = conn.execute("SELECT COUNT(*) FROM observations WHERE is_best = 1").fetchone()[0]
    old_runners = conn.execute(
        "SELECT COUNT(*) FROM observations WHERE is_best = 0 AND observed_at < ?",
        ((datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).fetchone()[0]
    assert old_runners > 0

    dry = compact.compact(conn, cfg, dry_run=True)
    assert dry.runner_ups_deleted == old_runners and not dry.vacuumed
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE is_best = 0").fetchone()[0] > 0

    st = compact.compact(conn, cfg)
    assert st.runner_ups_deleted == old_runners and st.vacuumed and st.size_after <= st.size_before
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE is_best = 1").fetchone()[0] == best_before
    assert conn.execute(
        "SELECT COUNT(*) FROM observations WHERE is_best = 0 AND observed_at < ?",
        ((datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fetch_attempts WHERE at < ?",
                        ((datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),)).fetchone()[0] == 0
    assert get_state(conn, compact.STATE_LAST_COMPACT)

    # The append-only guard is back in force.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM observations WHERE is_best = 1")


def test_zero_days_means_keep_forever(tmp_path):
    conn, cfg = _seeded(tmp_path, runner_up_days=0, attempts_days=0)
    st = compact.compact(conn, cfg)
    assert st.runner_ups_deleted == 0 and st.attempts_deleted == 0 and not st.vacuumed


def test_weekly_gating_in_the_daily_job(tmp_path):
    conn, cfg = _seeded(tmp_path, runner_up_days=30, attempts_days=30)
    assert compact.due(conn, cfg)
    res = jobs.run_daily(conn, cfg, lambda c: FakeSource(), verbose=False, dry_run=True)
    names = {s.name: s for s in res.stages}
    assert "compact" in names and "deleted" in names["compact"].summary
    assert not compact.due(conn, cfg)
    res = jobs.run_daily(conn, cfg, lambda c: FakeSource(), verbose=False, dry_run=True)
    assert {s.name: s for s in res.stages}["compact"].summary == "not due"
    assert not compact.due(conn, replace(cfg, retention=Retention(enabled=False)))
