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
    # Pinned: the feed row for 2026-12-25 must stay outside the grid whatever the real date is.
    conn, cfg = make_db(tmp_path, cfg, today=TODAY)
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


# ---------------------------------------------------------------------------
# M1: the bag filter goes out, and calendar prices are judged only against
# calendar prices
# ---------------------------------------------------------------------------

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def _decoded(body):
    import json
    import urllib.parse

    return json.loads(json.loads(urllib.parse.unquote(body[len("f.req="):-1]))[1])


def _qids(conn):
    return [r["id"] for r in conn.execute("SELECT id FROM queries WHERE active = 1 ORDER BY depart_date")]


def _add(conn, qid, cents, days_ago, source, is_best):
    from datetime import timedelta

    at = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("INSERT INTO observations (query_id, observed_at, price_cents, currency, is_best, raw, source) "
                 "VALUES (?, ?, ?, 'USD', ?, '{}', ?)", (qid, at, cents, is_best, source))
    conn.commit()


def test_sweep_sends_the_bag_filter_and_records_what_the_price_means(tmp_path):
    import json

    conn, cfg = make_db(tmp_path, _cfg(confirm_budget=0), today=TODAY)
    q = conn.execute("SELECT depart_date, return_date FROM queries WHERE active = 1 ORDER BY depart_date").fetchone()
    t = FakeTransport([(200, None, _batch([(q["depart_date"], q["return_date"], 300)]))])
    sweep.run(conn, cfg, transport=t, now=NOW, sleeper=lambda s: None, verbose=False)
    assert _decoded(t.calls[0]["data"])[1][10] == [1, 0]                      # cfg.search.carry_on_bags = 1
    raw = json.loads(conn.execute("SELECT raw FROM observations WHERE source = 'google_calendar'").fetchone()[0])
    assert raw["filters"] == {"bags": True, "exclude_basic": False}

    cfg0 = replace(cfg, search=replace(cfg.search, carry_on_bags=0))
    t0 = FakeTransport([(200, None, _batch([]))])
    sweep.run(conn, cfg0, transport=t0, now=NOW, sleeper=lambda s: None, verbose=False)
    assert _decoded(t0.calls[0]["data"])[1][10] is None


def test_promising_rules(tmp_path):
    conn, cfg = make_db(tmp_path, _cfg(), today=TODAY)
    a, b, c = _qids(conn)[:3]

    # 1. at or under the target, with no history at all
    assert sweep.promising(conn, cfg, a, 14000, 14000, now=NOW) == (True, "under target")

    # 2. far under the date pair's OWN calendar median (needs three points in 30 days)
    for days, cents in ((1, 30000), (5, 31000), (9, 29000)):
        _add(conn, a, cents, days, "google_calendar", 0)
    ok, why = sweep.promising(conn, cfg, a, 26000, 14000, now=NOW)
    assert ok and why == "13% under its 30-day calendar median"
    assert sweep.promising(conn, cfg, a, 28000, 14000, now=NOW) == (False, "not notable")   # only 7% under

    # Two points are not enough, and points older than 30 days do not count.
    _add(conn, b, 30000, 1, "google_calendar", 0)
    _add(conn, b, 30000, 2, "google_calendar", 0)
    _add(conn, b, 30000, 45, "google_calendar", 0)
    assert sweep.promising(conn, cfg, b, 20000, 14000, now=NOW)[0] is False

    # The detail series is never the yardstick: a calendar price far under the
    # results-page prices (which exclude basic economy) is not a bargain by itself.
    for days in (1, 2, 3, 4):
        _add(conn, c, 39400, days, "fake", 1)
    assert sweep.promising(conn, cfg, c, 30400, 14000, now=NOW) == (False, "not notable")

    # 3. one of the three cheapest dates in its window
    ok, why = sweep.promising(conn, cfg, c, 30400, 14000, now=NOW, cheapest_in_window=True)
    assert ok and "3 cheapest" in why


def test_three_cheapest_dates_of_a_window_are_confirmed(tmp_path):
    conn, cfg = make_db(tmp_path, replace(_cfg(confirm_budget=6), max_days_ahead=70), today=TODAY)
    rows = conn.execute("SELECT id, depart_date, return_date FROM queries WHERE active = 1 ORDER BY depart_date").fetchall()
    assert len(rows) >= 5
    prices = [400, 380, 300, 310, 290] + [500] * (len(rows) - 5)          # all over the $140 SLC target
    t = FakeTransport([(200, None, _batch([(r["depart_date"], r["return_date"], p) for r, p in zip(rows, prices)]))])
    fake = FakeSource(default=[39400])
    stats = sweep.run(conn, cfg, transport=t, confirm_source=fake, now=NOW, sleeper=lambda s: None, verbose=False)
    assert sorted(stats.confirm_ids) == sorted([rows[2]["id"], rows[3]["id"], rows[4]["id"]])
    assert stats.confirm_stats.succeeded == 3
