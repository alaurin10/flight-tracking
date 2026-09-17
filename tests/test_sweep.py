"""Calendar sweep: planning, storage as context rows, confirmation, blocking."""

from dataclasses import replace
from datetime import date, datetime, timezone

from flighttrack import fetch, sweep
from flighttrack.config import Calendar, Pattern
from flighttrack.source import FakeSource
from helpers import FakeTransport, make_config, make_db

TODAY = date(2026, 9, 17)


def _batch(rows):
    import json

    inner = json.dumps([None, [[d, r, [[None, p]], 1] for d, r, p in rows]])
    return ")]}'\n\n1\n" + json.dumps([["wrb.fr", None, inner, None, None, None, "generic"]]) + "\n"


def _cfg(**cal):
    cfg = make_config(patterns=[Pattern("wknd", "FRI", 3, routes=("SLC",))], max_days_ahead=40)
    return replace(cfg, calendar=Calendar(enabled=True, **cal))


def test_plan_covers_each_route_pattern_window():
    jobs = sweep.plan(_cfg(), TODAY)
    assert len(jobs) == 1 and jobs[0].route.dest == "SLC" and jobs[0].pattern.name == "wknd"
    assert jobs[0].start >= "2026-10-08"


def test_sweep_stores_context_rows_and_confirms_candidates(tmp_path):
    cfg = _cfg(confirm_budget=1)
    conn, cfg = make_db(tmp_path, cfg)
    q = conn.execute("SELECT depart_date, return_date FROM queries WHERE active = 1 ORDER BY depart_date").fetchall()
    d0, r0 = q[0]["depart_date"], q[0]["return_date"]
    d1, r1 = q[1]["depart_date"], q[1]["return_date"]
    t = FakeTransport([(200, None, _batch([(d0, r0, 120), (d1, r1, 260), ("2026-12-25", "2026-12-28", 50)]))])
    fake = FakeSource(default=[12500])
    stats = sweep.run(conn, cfg, transport=t, confirm_source=fake, now=datetime.combine(TODAY, datetime.min.time(), timezone.utc),
                      sleeper=lambda s: None, verbose=False)
    assert stats.ok == 1 and stats.dates_priced == 3 and stats.matched == 2 and stats.written == 2
    rows = conn.execute("SELECT price_cents, is_best, source FROM observations WHERE source = 'google_calendar' ORDER BY price_cents").fetchall()
    assert [(r["price_cents"], r["is_best"]) for r in rows] == [(12000, 0), (26000, 0)]
    # $120 is under the $140 SLC target → confirmed with a real (fake) fetch, which is the is_best series
    assert len(stats.confirm_ids) == 1 and stats.confirm_stats.succeeded == 1
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE is_best = 1 AND source = 'fake'").fetchone()[0] == 1
    assert "f.req=" in t.calls[0]["data"]


def test_sweep_blocked_enters_shared_cooldown(tmp_path):
    conn, cfg = make_db(tmp_path, _cfg())
    t = FakeTransport([(429, None, "no")])
    stats = sweep.run(conn, cfg, transport=t, confirm_source=FakeSource(), sleeper=lambda s: None, verbose=False)
    assert stats.blocked and fetch.cooldown_active(conn)
    s2 = fetch.run(conn, cfg, FakeSource(), sleeper=lambda s: None, verbose=False)
    assert s2.skipped


def test_sweep_layout_failure_is_logged_not_fatal(tmp_path):
    conn, cfg = make_db(tmp_path, _cfg())
    t = FakeTransport([(200, None, "<html>nothing</html>")])
    stats = sweep.run(conn, cfg, transport=t, confirm_source=FakeSource(), sleeper=lambda s: None, verbose=False)
    assert stats.failed == 1 and not stats.blocked
    assert conn.execute("SELECT outcome FROM fetch_attempts").fetchone()[0] == "layout"


def test_rotation_across_runs(tmp_path):
    cfg = replace(make_config(patterns=[Pattern("a", "FRI", 3), Pattern("b", "SAT", 7)], max_days_ahead=40),
                  calendar=Calendar(enabled=True, max_calls_per_run=2))
    conn, cfg = make_db(tmp_path, cfg)
    jobs = sweep.plan(cfg, TODAY)
    assert len(jobs) == 6
    first = sweep._rotate(jobs, conn, 2)
    second = sweep._rotate(jobs, conn, 2)
    assert first != second and len(first) == len(second) == 2
