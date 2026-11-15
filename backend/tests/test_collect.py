"""What the worker fetches next (fetch.select_due's order), the rolling budget, the
backup and the housekeeping (M4)."""

from datetime import UTC, datetime, timedelta

import pytest
import yaml
from helpers import FakeClock, make_config, make_db

from flighttrack import fetch
from flighttrack.config import Collect, ConfigError, Trip, load
from flighttrack.settings import Settings
from flighttrack.source import FakeSource
from flighttrack.store import backup, retention
from flighttrack.store.db import iso, set_clock

NOW = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
TODAY = NOW.date()


@pytest.fixture
def db(tmp_path):
    set_clock(FakeClock(NOW))
    trip = Trip("japan", "HND", (TODAY + timedelta(days=40)).isoformat(), None)
    cfg = make_config(trips=[trip], collect=Collect(top_dates_per_search=1))
    return make_db(tmp_path, cfg, today=TODAY)


def ids(due):
    return [d.query_id for d in due]


def trip_qid(conn):
    return conn.execute("SELECT query_id FROM watch_queries WHERE trip_id IS NOT NULL").fetchone()[
        0
    ]


def mark_fetched(conn, when, qids=None):
    if qids is None:
        conn.execute("UPDATE queries SET last_fetch_at = ?", (iso(when),))
    else:
        conn.executemany(
            "UPDATE queries SET last_fetch_at = ? WHERE id = ?", [(iso(when), q) for q in qids]
        )
    conn.commit()


def test_order_requested_trip_flagged_top_dates_then_rotation(db):
    conn, cfg = db
    all_ids = ids(fetch.select_due(conn, cfg, NOW, limit=999))
    trip = trip_qid(conn)
    assert all_ids[0] == trip  # never fetched trips first, even before never-fetched others

    # Everything fetched 21 hours ago: only the trip (20 h refresh) is due...
    mark_fetched(conn, NOW - timedelta(hours=21))
    assert ids(fetch.select_due(conn, cfg, NOW, limit=999)) == [trip]

    # ...plus each search's cheapest date (priced by a calendar estimate here).
    search_q = conn.execute(
        "SELECT query_id FROM watch_queries WHERE search_id IS NOT NULL ORDER BY query_id LIMIT 1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO observations (query_id, observed_at, price_cents, currency, is_best, raw,"
        " source) VALUES (?, ?, 100, 'USD', 0, '{}', 'google_calendar')",
        (search_q, iso(NOW - timedelta(hours=21))),
    )
    conn.commit()
    assert ids(fetch.select_due(conn, cfg, NOW, limit=999)) == [trip, search_q]

    # A sweep flag and a person's request go in front, in that order.
    others = [q for q in all_ids if q not in (trip, search_q)]
    fetch.flag_from_sweep(conn, [others[0]], NOW - timedelta(hours=1))
    fetch.request_queries(conn, [others[1]], NOW - timedelta(minutes=5))
    assert ids(fetch.select_due(conn, cfg, NOW, limit=999)) == [
        others[1],
        trip,
        others[0],
        search_q,
    ]

    # Once fetched after being asked for / flagged, they drop back.
    mark_fetched(conn, NOW, [others[0], others[1]])
    assert ids(fetch.select_due(conn, cfg, NOW, limit=999)) == [trip, search_q]
    assert fetch.prune_requested(conn, NOW) == []


def test_the_rotation_is_at_least_weekly_oldest_first(db):
    conn, cfg = db
    all_ids = ids(fetch.select_due(conn, cfg, NOW, limit=999))
    mark_fetched(conn, NOW - timedelta(days=8))
    mark_fetched(conn, NOW - timedelta(days=9), [all_ids[-1]])
    due = ids(fetch.select_due(conn, cfg, NOW, limit=999))
    assert set(due) == set(all_ids)
    assert due.index(all_ids[-1]) < due.index(all_ids[-2])  # older first (after the tiers)


def test_query_ids_still_selects_exactly_those(db):
    conn, cfg = db
    some = ids(fetch.select_due(conn, cfg, NOW, limit=999))[3:5]
    mark_fetched(conn, NOW)
    assert sorted(ids(fetch.select_due(conn, cfg, NOW, query_ids=some))) == sorted(some)


def test_budget_counts_google_requests_in_the_last_24_hours(db):
    conn, cfg = db
    cfg = make_config(collect=Collect(daily_request_budget=4))
    for hours, source in ((25, "google_html"), (23, "google_html"), (2, "google_calendar"),
                          (1, "serpapi"), (1, "canary"), (0, "fast_flights")):  # fmt: skip
        conn.execute(
            "INSERT INTO fetch_attempts (at, source, outcome) VALUES (?, ?, 'ok')",
            (iso(NOW - timedelta(hours=hours)), source),
        )
    conn.commit()
    assert fetch.budget_used(conn, NOW) == 4  # not the 25-hour-old one, not SerpApi
    assert fetch.budget_remaining(conn, cfg, NOW) == 0
    assert fetch.budget_frees_at(conn, cfg, NOW) == NOW + timedelta(hours=1)
    assert fetch.budget_remaining(conn, cfg, NOW + timedelta(hours=1, seconds=1)) == 1


def test_max_requests_stops_a_run_without_counting_a_failure(db):
    conn, cfg = db
    src = FakeSource()
    stats = fetch.run(conn, cfg, src, now=NOW, limit=5, max_requests=2, verbose=False)
    assert len(src.calls) == 2 and stats.budget_stop and not stats.blocked
    assert stats.attempted == 2 and stats.failed == 0


def test_max_requests_covers_a_retry(db):
    conn, cfg = db
    first = fetch.select_due(conn, cfg, NOW, limit=1)[0]
    src = FakeSource({(first.route.dest, first.depart_date, first.return_date): "network"})
    stats = fetch.run(
        conn, cfg, src, now=NOW, limit=5, max_requests=1, verbose=False, sleeper=lambda s: None
    )
    assert len(src.calls) == 1 and stats.budget_stop and stats.retries == 0


def test_collect_tunables_load_and_are_checked(tmp_path):
    path = tmp_path / "flighttrack.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "collect": {
                    "daily_request_budget": 80,
                    "batch_size": 4,
                    "batch_gap_minutes": [20, 30],
                    "sweep_at": "05:30",
                    "canary_dest": "sfo",
                }
            }
        )
    )
    c = load(path, settings=Settings()).collect
    assert (c.daily_request_budget, c.batch_size, c.batch_gap_minutes) == (80, 4, (20.0, 30.0))
    assert (c.sweep_at, c.canary_dest, c.trip_refresh_hours) == ("05:30", "SFO", 20)
    for bad in (
        {"batch_gap_minutes": [30, 20]},
        {"sweep_at": "25:00"},
        {"daily_request_budget": 0},
    ):
        path.write_text(yaml.safe_dump({"collect": bad}))
        with pytest.raises(ConfigError, match="collect"):
            load(path, settings=Settings())


def test_shipped_tunables_have_the_collect_section():
    from flighttrack.settings import REPO_ROOT

    raw = yaml.safe_load((REPO_ROOT / "config" / "flighttrack.yaml").read_text())
    assert raw["collect"] == {
        "daily_request_budget": 50,
        "batch_size": 5,
        "batch_gap_minutes": [25, 45],
        "sweep_at": "04:10",
        "trip_refresh_hours": 20,
        "top_dates_per_search": 3,
        "canary_dest": "LAX",
    }


def test_backup_copies_the_database_and_the_watches_and_keeps_14(db, tmp_path):
    conn, cfg = db
    out = tmp_path / "backups"
    for i in range(16):
        backup.backup(conn, cfg, out, today=TODAY + timedelta(days=i))
    dbs = backup.list_backups(out)
    assert len(dbs) == 14 and dbs[-1].name == f"flights-{(TODAY + timedelta(days=15)):%Y%m%d}.db"
    assert len(list(out.glob("tracked-*.yaml"))) == 14
    tracked = yaml.safe_load(sorted(out.glob("tracked-*.yaml"))[-1].read_text())
    assert tracked["trips"][0]["name"] == "japan"
    from flighttrack.store.db import connect

    copy = connect(dbs[-1], readonly=True)
    assert (
        copy.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
        == conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
    )


def test_retention_removes_old_records_but_never_the_price_series(db):
    conn, cfg = db
    old, recent = iso(NOW - timedelta(days=200)), iso(NOW - timedelta(days=10))
    qid = trip_qid(conn)
    conn.execute(
        "INSERT INTO observations (query_id, observed_at, price_cents, currency, is_best, raw) "
        "VALUES (?, ?, 50000, 'USD', 1, '{}')",
        (qid, old),
    )
    for when in (old, recent):
        conn.execute(
            "INSERT INTO notifications (kind, title, body, created_at) VALUES ('price','t','b',?)",
            (when,),
        )
        conn.execute(
            "INSERT INTO jobs (kind, status, created_at) VALUES ('run_batch', 'done', ?)", (when,)
        )
        conn.execute("INSERT INTO run_log (started_at, finished_at) VALUES (?, ?)", (when, when))
    kept_run = conn.execute(
        "INSERT INTO run_log (started_at, finished_at) VALUES (?, ?)", (old, old)
    ).lastrowid
    conn.execute(
        "INSERT INTO fetch_attempts (run_id, at, source, outcome) VALUES (?, ?, 'x', 'ok')",
        (kept_run, recent),
    )
    conn.commit()

    out = retention.prune(conn, cfg, now=NOW)
    assert (out["notifications"], out["jobs"], out["run_log"]) == (1, 1, 1)
    assert conn.execute("SELECT COUNT(*) FROM run_log WHERE id = ?", (kept_run,)).fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE is_best = 1").fetchone()[0] == 1
