"""M3: trips, searches and deal watches in the database, and import-config."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from helpers import add_obs, load_old, make_config, make_db

from flighttrack import report, watches
from flighttrack.config import read_watch_file
from flighttrack.store.db import open_db

FIXTURES = Path(__file__).parent / "fixtures"
OLD_CONFIG = FIXTURES / "config_m1.yaml"  # the config.yaml shipped up to M2
TODAY = date(2026, 11, 14)  # the day docs/PLAN.md counted 154 date pairs


def _imported(tmp_path: Path, name: str = "f.db") -> tuple[sqlite3.Connection, object]:
    cfg = load_old(OLD_CONFIG, strict_secrets=False)
    conn = open_db(tmp_path / name)
    watches.import_watch_file(conn, cfg, read_watch_file(OLD_CONFIG), today=TODAY)
    return conn, cfg


def _active(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM queries q JOIN routes r ON r.id = q.route_id "
        "WHERE q.active = 1 AND r.active = 1"
    ).fetchone()[0]


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    tables = ("routes", "queries", "watch_queries", "trips", "searches", "deal_watches")
    return {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in tables
    }


# ---------------------------------------------------------------------------
# acceptance
# ---------------------------------------------------------------------------


def test_old_shipped_config_imports_to_154_active_queries(tmp_path: Path) -> None:
    conn, _ = _imported(tmp_path)
    assert _active(conn) == 154
    assert [t.slug for t in watches.list_trips(conn)] == ["japan"]
    searches = {s.slug: s for s in watches.list_searches(conn)}
    assert set(searches) == {"ski_weekend", "ski_long_weekend", "tokyo_week", "tokyo_10"}
    # A pattern over HND and NRT becomes one search with both destinations.
    assert searches["tokyo_week"].destinations == ["HND", "NRT"]
    # The route target carries over when every destination shares it...
    assert searches["ski_weekend"].target_price == 14000
    # ...otherwise each route keeps its own, and its dates fall back on it.
    assert searches["tokyo_week"].target_price is None
    hnd, nrt = (
        conn.execute(
            "SELECT q.id FROM queries q JOIN routes r ON r.id = q.route_id "
            "WHERE q.pattern = 'tokyo_week' AND r.destination = ? AND q.active = 1 LIMIT 1",
            (dest,),
        ).fetchone()[0]
        for dest in ("HND", "NRT")
    )
    assert watches.target_for(conn, hnd) == 65000 and watches.target_for(conn, nrt) == 60000
    japan = conn.execute("SELECT id FROM queries WHERE pattern = 'japan'").fetchone()[0]
    assert watches.target_for(conn, japan) == 80000
    by_dest = dict(
        conn.execute(
            "SELECT r.destination, COUNT(*) FROM queries q JOIN routes r ON r.id = q.route_id "
            "WHERE q.active = 1 GROUP BY r.destination"
        ).fetchall()
    )
    assert by_dest["SLC"] == 25 and by_dest["HND"] + by_dest["NRT"] == 129  # 128 + the trip
    assert len(watches.list_deal_watches(conn)) == 2
    assert len(watches.list_deal_feeds(conn)) == 2


def test_importing_twice_changes_nothing(tmp_path: Path) -> None:
    conn, cfg = _imported(tmp_path)
    before = _snapshot(conn)
    summary = watches.import_watch_file(conn, cfg, read_watch_file(OLD_CONFIG), today=TODAY)
    assert not summary.changed
    assert summary.unchanged["trips"] == 1 and summary.unchanged["searches"] == 4
    assert _snapshot(conn) == before


def test_pausing_a_search_deactivates_only_unshared_queries(tmp_path: Path) -> None:
    conn, cfg = _imported(tmp_path)
    ski = watches.get_search(conn, "ski_weekend")
    first = conn.execute(
        "SELECT q.depart_date, q.return_date FROM queries q WHERE q.pattern = 'ski_weekend' "
        "AND q.active = 1 ORDER BY q.depart_date LIMIT 1"
    ).fetchone()
    # A fixed trip on one of the search's date pairs: that pair is wanted twice.
    watches.create_trip(
        conn,
        cfg,
        {
            "name": "Ski with friends",
            "origin": "SEA",
            "destination": "SLC",
            "depart_date": first["depart_date"],
            "return_date": first["return_date"],
        },
        today=TODAY,
    )
    shared = conn.execute(
        "SELECT id FROM queries WHERE depart_date = ? AND return_date = ?",
        (first["depart_date"], first["return_date"]),
    ).fetchone()[0]
    links = conn.execute(
        "SELECT COUNT(*) FROM watch_queries WHERE query_id = ?", (shared,)
    ).fetchone()[0]
    assert links == 2

    watches.set_search_status(conn, cfg, ski.id, "paused", today=TODAY)

    states = dict(
        conn.execute(
            "SELECT q.id, q.active FROM queries q JOIN routes r ON r.id = q.route_id "
            "WHERE r.destination = 'SLC'"
        ).fetchall()
    )
    assert states.pop(shared) == 1  # the trip still wants it
    ski_only = conn.execute(
        "SELECT COUNT(*) FROM queries WHERE pattern = 'ski_weekend'"
    ).fetchone()[0]
    assert ski_only == 12 and not any(
        states[qid]
        for (qid,) in conn.execute("SELECT id FROM queries WHERE pattern = 'ski_weekend'")
    )
    # The other ski search's pairs are untouched, and the shared pair is now the trip's.
    assert _active(conn) == 154 - 12  # 13 Fridays, less the one the trip shares
    assert conn.execute("SELECT pattern FROM queries WHERE id = ?", (shared,)).fetchone()[0] == (
        "ski-with-friends"
    )


def test_a_trip_over_the_limit_is_refused_with_the_count(tmp_path: Path) -> None:
    conn, cfg = make_db(tmp_path, make_config(patterns=[]), today=TODAY)
    base = TODAY + timedelta(days=60)

    def trip(i: int) -> dict[str, object]:
        d = base + timedelta(days=i)
        return {
            "name": f"Trip {i}",
            "origin": "SEA",
            "destination": "SLC",
            "depart_date": d.isoformat(),
            "return_date": (d + timedelta(days=3)).isoformat(),
        }

    for i in range(watches.MAX_ACTIVE_TRIPS):
        watches.create_trip(conn, cfg, trip(i), today=TODAY, expand=False)
    with pytest.raises(watches.LimitError, match="21 active trips; the limit is 20"):
        watches.create_trip(conn, cfg, trip(99), today=TODAY)
    assert len(watches.list_trips(conn)) == 20  # nothing half-saved
    # A paused one does not count, and it may not be resumed past the limit.
    paused = watches.create_trip(conn, cfg, {**trip(98), "status": "paused"}, today=TODAY)
    with pytest.raises(watches.LimitError, match="21 active trips"):
        watches.set_trip_status(conn, cfg, paused.id, "active", today=TODAY)


def test_a_search_over_the_pair_limit_is_refused_with_the_count(tmp_path: Path) -> None:
    conn, cfg = make_db(tmp_path, make_config(patterns=[], max_days_ahead=240), today=TODAY)
    big = {
        "name": "Everywhere, every day",
        "origin": "SEA",
        "destinations": ["HND", "NRT", "SLC"],
        "depart_dows": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
        "nights": 3,
    }
    with pytest.raises(watches.LimitError, match=r"track 660 date pairs in all; the limit is 400"):
        watches.create_search(conn, cfg, big, today=TODAY)
    assert watches.list_searches(conn) == [] and _active(conn) == 0


def test_history_survives_migration_and_import(tmp_path: Path) -> None:
    """A database from before M3 (M1 schema, old pattern names) keeps the same history
    for the same date pairs after migrating and importing the old config."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript((FIXTURES / "schema_m1.sql").read_text())
    old.executescript(
        """
        ALTER TABLE observations ADD COLUMN source TEXT;
        ALTER TABLE run_log ADD COLUMN kind TEXT NOT NULL DEFAULT 'fetch';
        ALTER TABLE run_log ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0;
        INSERT INTO routes (id, origin, destination, label, target_price, priority)
            VALUES (1, 'SEA', 'HND', 'Tokyo', 65000, 1), (2, 'SEA', 'SLC', 'Salt Lake City',
            14000, 1);
        INSERT INTO queries (id, route_id, depart_date, return_date, pattern, last_fetch_at)
            VALUES (7, 1, '2027-04-10', '2027-04-24', 'japan', '2026-11-13T10:00:00Z'),
                   (8, 2, '2027-01-15', '2027-01-18', 'ski_weekend', '2026-11-13T10:00:00Z');
        INSERT INTO observations (query_id, observed_at, price_cents, is_best, source)
            VALUES (7, '2026-11-12T10:00:00Z', 91200, 1, 'google_html'),
                   (7, '2026-11-13T10:00:00Z', 89900, 1, 'google_html'),
                   (8, '2026-11-13T10:00:00Z', 39400, 1, 'google_html');
        """
    )
    old.commit()
    old.close()

    conn = open_db(path)  # migrates
    before = {q: report.history(conn, q) for q in (7, 8)}
    cfg = load_old(OLD_CONFIG, strict_secrets=False)
    watches.import_watch_file(conn, cfg, read_watch_file(OLD_CONFIG), today=TODAY)

    assert {q: report.history(conn, q) for q in (7, 8)} == before
    rows = conn.execute("SELECT id, active, pattern FROM queries WHERE id IN (7, 8)").fetchall()
    assert {(r["id"], r["active"], r["pattern"]) for r in rows} == {
        (7, 1, "japan"),
        (8, 1, "ski_weekend"),
    }
    assert conn.execute("SELECT label FROM routes WHERE id = 1").fetchone()[0] == "Tokyo"
    grid = {r.query_id: r for r in report.grid(conn, pattern="japan")}
    assert grid[7].current_cents == 89900 and grid[7].target_price == 80000
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3


# ---------------------------------------------------------------------------
# watches.py
# ---------------------------------------------------------------------------


def _trip(**over: object) -> dict[str, object]:
    return {
        "name": "Lisbon",
        "origin": "SEA",
        "destination": "LIS",
        "depart_date": "2027-03-05",
        "return_date": "2027-03-12",
        **over,
    }


@pytest.mark.parametrize(
    "over, match",
    [
        ({"destination": "XQX"}, "not an airport with scheduled flights"),
        ({"destination": "Lisbon"}, "not a three-letter airport code"),
        ({"destination": "SEA"}, "same as the origin"),
        ({"return_date": "2027-03-01"}, "return date must be after"),
        ({"depart_date": "2027-02-30"}, "not a real date"),
        ({"depart_date": "5 March"}, "YYYY-MM-DD"),
        ({"target_price": -100}, "target_price"),
        ({"name": ""}, "name"),
        ({"slug": "Has Spaces"}, "slug: use lower-case"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_trip_validation(tmp_path: Path, over: dict[str, object], match: str) -> None:
    conn, cfg = make_db(tmp_path, make_config(patterns=[]))
    with pytest.raises(watches.WatchError, match=match):
        watches.create_trip(conn, cfg, _trip(**over))


@pytest.mark.parametrize(
    "over, match",
    [
        ({"depart_dows": ["FUNDAY"]}, "FUN is not a weekday"),
        ({"nights": -1}, "nights"),
        ({"destinations": []}, "destinations"),
        ({"destinations": ["SEA"]}, "both the origin and a destination"),
        ({"window_start": "2027-03-01", "window_end": "2027-01-01"}, "starts after it ends"),
        ({"priority": 7}, "priority"),
    ],
)
def test_search_validation(tmp_path: Path, over: dict[str, object], match: str) -> None:
    conn, cfg = make_db(tmp_path, make_config(patterns=[]))
    data = {
        "name": "Weekends",
        "origin": "SEA",
        "destinations": ["SLC"],
        "depart_dows": ["fri"],
        "nights": 3,
        **over,
    }
    with pytest.raises(watches.WatchError, match=match):
        watches.create_search(conn, cfg, data)


def test_slugs_are_derived_and_unique_across_trips_and_searches(tmp_path: Path) -> None:
    conn, cfg = make_db(tmp_path, make_config(patterns=[]))
    a = watches.create_trip(conn, cfg, _trip(name="Lisbon, spring!"))
    b = watches.create_trip(
        conn, cfg, _trip(name="Lisbon spring", depart_date="2027-04-02", return_date=None)
    )
    assert (a.slug, b.slug) == ("lisbon-spring", "lisbon-spring-2")
    s = watches.create_search(
        conn,
        cfg,
        {
            "name": "Lisbon spring",
            "origin": "SEA",
            "destinations": ["LIS"],
            "depart_dows": ["SAT"],
            "nights": 7,
        },
    )
    assert s.slug == "lisbon-spring-3"
    with pytest.raises(watches.WatchError, match="already used"):
        watches.update_search(conn, cfg, s.id, {**_fields(s), "slug": "lisbon-spring"})


def _fields(s: watches.Search) -> dict[str, object]:
    return s.model_dump(include=set(watches.SearchIn.model_fields))


def test_saving_a_trip_expands_it_at_once_and_deleting_keeps_history(tmp_path: Path) -> None:
    conn, cfg = make_db(tmp_path, make_config(patterns=[]))
    future = (date.today() + timedelta(days=90)).isoformat()
    t = watches.create_trip(conn, cfg, _trip(depart_date=future, return_date=None))
    q = conn.execute(
        "SELECT q.id, q.active, q.pattern, r.label FROM queries q "
        "JOIN routes r ON r.id = q.route_id WHERE q.depart_date = ?",
        (future,),
    ).fetchone()
    assert q["active"] == 1 and q["pattern"] == t.slug and q["label"] == "Lisbon"  # the city
    add_obs(conn, q["id"], 70000)

    watches.delete_trip(conn, cfg, t.id)
    row = conn.execute("SELECT active FROM queries WHERE id = ?", (q["id"],)).fetchone()
    assert row["active"] == 0
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert conn.execute("SELECT active FROM routes").fetchone()[0] == 0


def test_target_is_the_lowest_of_the_watches_else_the_routes(tmp_path: Path) -> None:
    conn, cfg = make_db(tmp_path, make_config(patterns=[]), today=TODAY)
    s = watches.create_search(
        conn,
        cfg,
        {
            "name": "SLC weekends",
            "origin": "SEA",
            "destinations": ["SLC"],
            "depart_dows": ["FRI"],
            "nights": 3,
        },
        today=TODAY,
    )
    qid, dep, ret = conn.execute(
        "SELECT id, depart_date, return_date FROM queries WHERE active = 1 "
        "ORDER BY depart_date LIMIT 1"
    ).fetchone()
    assert watches.target_for(conn, qid) == 14000  # the route's own (make_config's SLC)
    watches.update_search(conn, cfg, s.id, {**_fields(s), "target_price": 20000}, today=TODAY)
    assert watches.target_for(conn, qid) == 20000  # a watch's target wins over the route's
    watches.create_trip(
        conn,
        cfg,
        {
            "name": "Cheap one",
            "origin": "SEA",
            "destination": "SLC",
            "depart_date": dep,
            "return_date": ret,
            "target_price": 12000,
        },
        today=TODAY,
    )
    assert watches.target_for(conn, qid) == 12000  # the lowest of the two
    other = conn.execute(
        "SELECT id FROM queries WHERE active = 1 AND id != ? LIMIT 1", (qid,)
    ).fetchone()[0]
    assert watches.target_for(conn, other) == 20000


def test_export_then_import_round_trips(tmp_path: Path) -> None:
    import yaml

    from flighttrack.config import parse_watch_file

    conn, cfg = _imported(tmp_path)
    tokyo_10 = watches.get_search(conn, "tokyo_10").id
    watches.set_search_status(conn, cfg, tokyo_10, "paused", today=TODAY)
    exported = watches.export_watch_file(conn, cfg)
    text = yaml.safe_dump(exported, sort_keys=False)
    assert "2027-04-10" in text

    fresh = open_db(tmp_path / "fresh.db")
    watches.import_watch_file(fresh, cfg, parse_watch_file(yaml.safe_load(text)), today=TODAY)

    def shape(c: sqlite3.Connection) -> tuple[object, ...]:
        drop = {"id", "created_at", "updated_at"}
        return (
            [t.model_dump(exclude=drop) for t in watches.list_trips(c)],
            [s.model_dump(exclude=drop) for s in watches.list_searches(c)],
            [w.model_dump(exclude=drop) for w in watches.list_deal_watches(c)],
            [f.url for f in watches.list_deal_feeds(c)],
            _active(c),
        )

    assert shape(fresh) == shape(conn)


def test_deal_feeds_fall_back_to_the_tunables_until_the_table_has_one(tmp_path: Path) -> None:
    conn, cfg = make_db(tmp_path, make_config(patterns=[]))
    from dataclasses import replace

    from flighttrack.config import Deals

    cfg = replace(cfg, deals=Deals(enabled=True, feeds=("https://default.example/feed",)))
    assert watches.active_feed_urls(conn, cfg) == ["https://default.example/feed"]
    f = watches.create_deal_feed(conn, {"url": "https://mine.example/feed"})
    assert watches.active_feed_urls(conn, cfg) == ["https://mine.example/feed"]
    watches.set_deal_feed_status(conn, f.id, "paused")
    assert watches.active_feed_urls(conn, cfg) == []
    with pytest.raises(watches.WatchError, match="already a feed"):
        watches.create_deal_feed(conn, {"url": "https://mine.example/feed"})
    with pytest.raises(watches.WatchError, match="http"):
        watches.create_deal_feed(conn, {"url": "ftp://nope.example/feed"})
