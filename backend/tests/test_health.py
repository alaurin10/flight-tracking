"""Watchdog: silent failure must become a notification."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from flighttrack import fetch, health
from flighttrack.db import set_state, utcnow
from flighttrack.source import BlockedError, FakeSource
from helpers import make_config, make_db


def _file_cfg(tmp_path, **health_over):
    cfg = make_config(notify={"channel": "file", "file": {"path": str(tmp_path / "digest.md")}})
    if health_over:
        cfg = replace(cfg, health=replace(cfg.health, **health_over))
    return cfg


def test_fresh_database_is_ok(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    rep = health.check(conn, cfg)
    assert rep.ok and not rep.problems


def test_runs_without_any_observation_is_critical(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    fetch.run(conn, cfg, _Empty(), sleeper=lambda s: None, verbose=False)   # all empty
    conn.execute("DELETE FROM fetch_attempts")
    conn.commit()
    rep = health.check(conn, cfg)
    assert [p.code for p in rep.problems] == ["never_succeeded"] and not rep.ok


def test_stale_success_and_no_recent_run(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    fetch.run(conn, cfg, FakeSource(), sleeper=lambda s: None, verbose=False)
    fetch.run(conn, cfg, FakeSource(), sleeper=lambda s: None, verbose=False)
    later = datetime.now(timezone.utc) + timedelta(hours=72)
    rep = health.check(conn, cfg, now=later)
    codes = {p.code for p in rep.problems}
    assert "stale_success" in codes and "no_recent_run" in codes


def test_cooldown_and_all_failing(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    fetch.run(conn, cfg, _Blocked(), sleeper=lambda s: None, verbose=False)
    rep = health.check(conn, cfg)
    codes = {p.code for p in rep.problems}
    assert "cooldown" in codes
    # one blocked attempt only → not yet 'all_failing' (needs ≥2 attempts)
    assert "all_failing" not in codes


class _Empty:
    name = "e"

    def fetch(self, *a, **k):
        from flighttrack.source import NoResults

        raise NoResults("nothing")


class _Blocked:
    name = "b"

    def fetch(self, *a, **k):
        raise BlockedError("429")


def test_layout_failures_are_flagged(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    for _ in range(3):
        fetch.log_attempt(conn, None, None, "google_html", "layout", 100, "no blob")
    conn.commit()
    rep = health.check(conn, cfg)
    codes = {p.code for p in rep.problems}
    assert "layout" in codes and "all_failing" in codes


def test_notification_is_deduped_and_resent_on_change(tmp_path):
    cfg = _file_cfg(tmp_path)
    conn, cfg = make_db(tmp_path, cfg)
    for _ in range(3):
        fetch.log_attempt(conn, None, None, "google_html", "layout", 100, "no blob")
    conn.commit()
    digest = tmp_path / "digest.md"

    rep, sent = health.run(conn, cfg, verbose=False)
    assert sent and not rep.ok and "not collecting" in digest.read_text()
    rep, sent = health.run(conn, cfg, verbose=False)
    assert not sent                                     # same problems, inside renotify window

    set_state(conn, fetch.STATE_COOLDOWN_UNTIL, (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    set_state(conn, fetch.STATE_BLOCKED_STREAK, "3")
    rep, sent = health.run(conn, cfg, verbose=False)
    assert sent                                         # problem set changed → notify again


def test_warnings_alone_do_not_notify(tmp_path):
    cfg = _file_cfg(tmp_path)
    conn, cfg = make_db(tmp_path, cfg)
    set_state(conn, fetch.STATE_COOLDOWN_UNTIL, (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    set_state(conn, fetch.STATE_BLOCKED_STREAK, "1")
    rep, sent = health.run(conn, cfg, verbose=False)
    assert rep.ok and rep.problems and not sent


def test_dead_feed_is_a_warning(tmp_path):
    from flighttrack.config import DealWatch, Deals

    cfg = make_config()
    cfg = replace(cfg, deals=Deals(enabled=True, feeds=("https://x/feed",), watches=(DealWatch("w", ("Seattle",), ()),)))
    conn, cfg = make_db(tmp_path, cfg)
    conn.execute("INSERT INTO feed_log (feed, at, ok, items, error) VALUES ('https://x/feed', ?, 0, 0, 'boom')", (utcnow(),))
    conn.commit()
    rep = health.check(conn, cfg)
    assert any(p.code.startswith("feed_") and p.severity == "warn" for p in rep.problems)
