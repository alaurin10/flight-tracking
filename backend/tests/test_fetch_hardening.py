"""Fetcher: typed failures, cooldown state, retry, attempt log."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from flighttrack import fetch
from flighttrack.db import get_state
from flighttrack.source import BlockedError, FakeSource, LayoutError, NetworkError, Offer
from helpers import make_config, make_db


class Sequenced:
    """Raise/return a scripted sequence, then succeed forever."""

    name = "seq"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def fetch(self, cfg, route, depart, ret, limit):
        self.calls += 1
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        return [Offer(price_cents=40000, source=self.name)]


def _run(conn, cfg, source, **kw):
    sleeps = []
    stats = fetch.run(conn, cfg, source, sleeper=sleeps.append, verbose=False, **kw)
    return stats, sleeps


def test_first_block_aborts_and_starts_cooldown(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    s = Sequenced([BlockedError("429")])
    stats, _ = _run(conn, cfg, s)
    assert stats.blocked and stats.abort_reason == "blocked" and s.calls == 1
    assert stats.cooldown_until and fetch.cooldown_active(conn)
    assert get_state(conn, fetch.STATE_BLOCKED_STREAK) == "1"

    # The very next run skips itself.
    stats2, _ = _run(conn, cfg, s)
    assert stats2.skipped and stats2.attempted == 0 and s.calls == 1
    row = conn.execute("SELECT skipped, notes FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["skipped"] == 1 and "cooldown" in row["notes"]


def test_cooldown_escalates_then_clears_on_success(tmp_path):
    cfg = make_config()
    cfg = replace(cfg, fetch=replace(cfg.fetch, cooldown_hours=(1.0, 4.0, 12.0)))
    conn, cfg = make_db(tmp_path, cfg)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")

    fetch.run(conn, cfg, Sequenced([BlockedError("x")]), now=t0, sleeper=lambda s: None, verbose=False)
    assert get_state(conn, fetch.STATE_COOLDOWN_UNTIL) == iso(t0 + timedelta(hours=1))
    t1 = t0 + timedelta(hours=2)
    fetch.run(conn, cfg, Sequenced([BlockedError("x")]), now=t1, sleeper=lambda s: None, verbose=False)
    assert get_state(conn, fetch.STATE_COOLDOWN_UNTIL) == iso(t1 + timedelta(hours=4))
    t2 = t1 + timedelta(hours=8)
    fetch.run(conn, cfg, Sequenced([BlockedError("x")]), now=t2, sleeper=lambda s: None, verbose=False)
    assert get_state(conn, fetch.STATE_COOLDOWN_UNTIL) == iso(t2 + timedelta(hours=12))   # capped at the last entry
    t3 = t2 + timedelta(hours=13)
    stats = fetch.run(conn, cfg, FakeSource(), now=t3, sleeper=lambda s: None, verbose=False)
    assert stats.succeeded and not fetch.cooldown_active(conn, t3)
    assert get_state(conn, fetch.STATE_BLOCKED_STREAK) == "0"
    assert get_state(conn, fetch.STATE_LAST_SUCCESS)


def test_network_error_is_retried_once_then_counts(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    s = Sequenced([NetworkError("timeout"), None])   # fails once, then ok
    stats, sleeps = _run(conn, cfg, s, limit=1)
    assert stats.retries == 1 and stats.succeeded == 1 and stats.failed == 0
    assert cfg.fetch.retry_sleep_seconds in sleeps
    outcomes = [r["outcome"] for r in conn.execute("SELECT outcome FROM fetch_attempts ORDER BY id")]
    assert outcomes == ["network", "ok"]


def test_persistent_network_failure_aborts_with_cooldown(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    s = Sequenced([NetworkError("a"), NetworkError("b"), NetworkError("c"), NetworkError("d")])
    stats, _ = _run(conn, cfg, s)
    assert stats.blocked and stats.abort_reason == "network" and stats.cooldown_until
    assert stats.failed == 2 and stats.retries == 2 and s.calls == 4


def test_layout_abort_has_no_cooldown(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    stats, _ = _run(conn, cfg, Sequenced([LayoutError("a"), LayoutError("b")]))
    assert stats.blocked and stats.abort_reason == "layout"
    assert stats.cooldown_until is None and not fetch.cooldown_active(conn)
    note = conn.execute("SELECT notes FROM run_log ORDER BY id DESC LIMIT 1").fetchone()["notes"]
    assert "layout" in note


def test_attempt_log_and_source_column(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    stats, _ = _run(conn, cfg, FakeSource(), limit=2)
    rows = conn.execute("SELECT source, outcome, latency_ms FROM fetch_attempts").fetchall()
    assert len(rows) == 2 and all(r["outcome"] == "ok" and r["source"] == "fake" for r in rows)
    assert all(r["latency_ms"] is not None for r in rows)
    assert conn.execute("SELECT DISTINCT source FROM observations").fetchone()[0] == "fake"
    assert stats.sources_used == {"fake": 2}


def test_query_ids_bypass_the_due_test(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    stats, _ = _run(conn, cfg, FakeSource(), limit=1)
    qid = conn.execute("SELECT query_id FROM observations LIMIT 1").fetchone()[0]
    assert not [d for d in fetch.select_due(conn, cfg) if d.query_id == qid]        # just fetched → not due
    assert [d.query_id for d in fetch.select_due(conn, cfg, query_ids=[qid])] == [qid]
    stats, _ = _run(conn, cfg, FakeSource(), query_ids=[qid], limit=1, kind="confirm")
    assert stats.succeeded == 1
    assert conn.execute("SELECT kind FROM run_log ORDER BY id DESC LIMIT 1").fetchone()[0] == "confirm"


def test_ignore_cooldown_flag(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    _run(conn, cfg, Sequenced([BlockedError("x")]))
    stats, _ = _run(conn, cfg, FakeSource(), respect_cooldown=False, limit=1)
    assert stats.succeeded == 1
