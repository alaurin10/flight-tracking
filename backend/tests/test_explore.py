"""Explore boards (D2): the request layout, the reply parser on a real capture, which
boards and departures get priced, storing fares, reading boards back, and the worker's
explore lane. Offline: FakeTransport and the fixture in tests/fixtures/live-20261116."""

from __future__ import annotations

import json
import random
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from helpers import FakeClock, FakeTransport, make_config, make_db

from flighttrack import explore, fetch, pace, watches
from flighttrack.config import Explore, Fetch
from flighttrack.gflights import explore as gx
from flighttrack.notifier import Notifier
from flighttrack.scheduler import STATE_NEXT_BATCH, Worker
from flighttrack.settings import Settings
from flighttrack.source import FakeSource
from flighttrack.store.db import iso, set_clock, set_state

NOW = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
TODAY = NOW.date()
LIVE = Path(__file__).parent / "fixtures" / "live-20261116"
REPLY = (LIVE / "explore_sea_anywhere_3n.txt").read_text(encoding="utf-8")
GATED = (LIVE / "calendar_gated_sea_slc_3n.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def clock():
    c = FakeClock(NOW)
    set_clock(c)
    return c


def db(tmp_path, **over):
    cfg = make_config(
        explore=Explore(enabled=True),
        failure_dir=str(tmp_path / "failures"),
        fetch=Fetch(max_queries_per_run=5, min_sleep_seconds=0, max_sleep_seconds=0),
        **over,
    )
    return make_db(tmp_path, cfg, today=TODAY)


def decode(body: str) -> list:
    raw = urllib.parse.unquote(body.removeprefix("f.req=").rstrip("&"))
    return json.loads(json.loads(raw)[1])


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


def test_the_request_follows_the_captured_layout():
    outer = decode(gx.build_body("sea", "anywhere", "2027-01-15", 3, carry_on_bags=1))
    assert len(outer) == 12 and outer[10] == [447, 712] and outer[11] == 3
    inner = outer[3]
    assert len(inner) == 26 and inner[2] == 1  # round trip
    assert inner[5] == 1 and inner[6] == [1, 0, 0, 0] and inner[10] == [1, 0]
    assert (inner[17], inner[24], inner[25]) == (1, 1, 1)
    sl = inner[13][0]
    assert sl[0] == [[["SEA", 0]]] and sl[1] == [[["/m/02j71", 6]]]
    assert sl[2] == [4, 23, 3, 3] and sl[3] == 0 and sl[6] == "2027-01-15"

    one_way = decode(gx.build_body("SEA", "europe", "2027-01-15", None, max_stops=0))[3]
    assert one_way[2] == 2 and one_way[10] is None
    assert one_way[13][0][1] == [[["/m/02j9z", 6]]]
    assert one_way[13][0][2] is None and one_way[13][0][3] == 1  # nonstop


def test_place_tokens():
    assert gx.place_token("Asia") == ("/m/0j0k", 6)
    assert gx.place_token("lax") == ("LAX", 0)
    assert gx.place_token("/m/04jpl") == ("/m/04jpl", 6)
    with pytest.raises(ValueError, match="unknown Explore place"):
        gx.place_token("Atlantis")


def test_the_live_reply_parses():
    """SEA → Anywhere, 3 nights from 2027-01-15, captured 2026-11-16 (README)."""
    r = gx.parse_explore(REPLY)
    assert r.origin_name == "Seattle" and len(r.destinations) == 92
    priced = r.priced
    assert [(d.name, d.airport, d.price) for d in priced] == [
        ("Dubai", "DXB", 548),
        ("Mumbai", "BOM", 639),
        ("Lima", "LIM", 823),
        ("Athens", "ATH", 839),
        ("Buenos Aires", "EZE", 954),
        ("Johannesburg", "JNB", 1374),
    ]
    dubai = priced[0]
    assert (dubai.country, dubai.stops, dubai.duration_min) == ("United Arab Emirates", 1, 1440)
    assert dubai.airline_name == "Qatar Airways" and dubai.depart == "2027-01-15"
    assert dubai.return_date(3) == "2027-01-18"
    la = next(d for d in r.destinations if d.name == "Los Angeles")
    assert la.price is None and la.country == "United States"
    assert not gx.looks_gated(REPLY)


def test_a_declined_reply_has_no_destinations_and_looks_gated():
    assert gx.parse_explore(GATED).destinations == [] and gx.looks_gated(GATED)


# ---------------------------------------------------------------------------
# Boards and departures
# ---------------------------------------------------------------------------


def _watch(conn, name, destinations, **kw):
    return watches.create_deal_watch(
        conn, {"name": name, "origins": ["Seattle"], "destinations": destinations, **kw}
    )


def test_boards_come_from_the_settings_and_the_region_watches(tmp_path):
    conn, cfg = db(tmp_path)
    eu = _watch(conn, "Europe in summer", ["europe"], months=[6, 7, 8], max_price=70000)
    jp = _watch(conn, "Japan", ["Japan"], nights=10, origin="PDX")
    _watch(conn, "Lisbon", ["Lisbon", "Porto"])  # free words: posts only
    paused = _watch(conn, "Africa", ["africa"])
    watches.set_deal_watch_status(conn, paused.id, "paused")
    bs = {b.key: b for b in explore.boards(conn, cfg)}
    assert set(bs) == {"anywhere-3", "anywhere-7", f"watch-{eu.id}", f"watch-{jp.id}"}
    assert bs["anywhere-3"].title == "Anywhere from SEA · long weekends"
    e = bs[f"watch-{eu.id}"]
    assert (e.region, e.nights, e.months, e.words, e.max_price) == (
        "europe",
        7,
        (6, 7, 8),
        (),
        70000,
    )
    j = bs[f"watch-{jp.id}"]
    assert (j.region, j.nights, j.origin) == ("asia", 10, "PDX")
    assert j.keeps("Tokyo", "Japan") and not j.keeps("Seoul", "South Korea")


def test_departures_are_weekly_on_the_boards_weekday_within_the_horizon_and_months(tmp_path):
    _conn, cfg = db(tmp_path, max_days_ahead=120)
    weekend = explore.Board("b", "t", "SEA", "anywhere", 3)
    week = explore.Board("b", "t", "SEA", "anywhere", 7, months=(2,))
    fri = explore.board_departures(cfg, weekend, TODAY)
    assert all(date.fromisoformat(d).weekday() == 4 for d in fri)
    assert fri[0] >= (TODAY + timedelta(days=cfg.min_days_ahead)).isoformat()
    assert fri[-1] <= (TODAY + timedelta(days=cfg.max_days_ahead)).isoformat()
    sat = explore.board_departures(cfg, week, TODAY)
    assert sat and all(date.fromisoformat(d).weekday() == 5 for d in sat)
    assert all(d[5:7] == "02" for d in sat)


def test_two_boards_that_need_the_same_call_share_it(tmp_path):
    conn, cfg = db(tmp_path)
    _watch(conn, "Asia A", ["asia"], nights=7)
    _watch(conn, "Japan", ["japan"], nights=7)  # also asks Asia, 7 nights
    jobs = explore.plan(conn, cfg, TODAY)
    keys = [j.key() for j in jobs]
    assert len(keys) == len(set(keys))
    asia = [j for j in jobs if j.region == "asia"]
    assert asia and len(asia) == len(
        explore.board_departures(cfg, explore.boards(conn, cfg)[-1], TODAY)
    )


# ---------------------------------------------------------------------------
# Running calls and storing fares
# ---------------------------------------------------------------------------


def _job(depart="2027-01-15", nights=3):
    return explore.ExploreJob("SEA", "anywhere", nights, depart)


def test_a_call_stores_each_priced_place_once_a_day(tmp_path, clock):
    conn, cfg = db(tmp_path)
    t = FakeTransport([(200, None, REPLY)] * 3)
    stats = explore.run(conn, cfg, [_job()], transport=t, now=NOW, sleeper=lambda s: None)
    assert (stats.ok, stats.places, stats.priced, stats.written) == (1, 92, 6, 6)
    assert t.calls[0]["url"] == gx.RPC_URL and t.calls[0]["data"].startswith("f.req=")
    row = conn.execute("SELECT * FROM explore_prices WHERE name = 'Dubai'").fetchone()
    assert (row["price_cents"], row["airport"], row["region"], row["nights"]) == (
        54800,
        "DXB",
        "anywhere",
        3,
    )
    att = conn.execute("SELECT source, outcome FROM fetch_attempts").fetchall()
    assert [tuple(a) for a in att] == [("google_explore", "ok")]
    scan = conn.execute("SELECT kind, last_outcome, last_run_at FROM scans").fetchone()
    assert tuple(scan) == ("explore", "ok", iso(NOW))
    # The same fares an hour later are not written again; a day later they are.
    explore.run(conn, cfg, [_job()], transport=t, now=NOW + timedelta(hours=1))
    assert conn.execute("SELECT COUNT(*) FROM explore_prices").fetchone()[0] == 6
    explore.run(conn, cfg, [_job()], transport=t, now=NOW + timedelta(hours=21))
    assert conn.execute("SELECT COUNT(*) FROM explore_prices").fetchone()[0] == 12
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM explore_prices")


def test_a_block_starts_the_cooldown_and_a_declined_reply_stops_the_run(tmp_path):
    conn, cfg = db(tmp_path)
    t = FakeTransport([(429, None, "slow down")])
    stats = explore.run(conn, cfg, [_job(), _job("2027-01-22")], transport=t, now=NOW)
    assert stats.blocked and stats.calls == 1 and fetch.cooldown_active(conn, NOW)
    assert conn.execute("SELECT last_run_at FROM scans").fetchone()[0] is None

    conn2, cfg2 = db(tmp_path / "b")
    t2 = FakeTransport([(200, None, GATED)] * 2)
    stats = explore.run(conn2, cfg2, [_job(), _job("2027-01-22")], transport=t2, now=NOW)
    assert stats.gated and stats.calls == 1 and not fetch.cooldown_active(conn2, NOW)
    assert conn2.execute("SELECT outcome FROM fetch_attempts").fetchone()[0] == "gated"


def test_due_departures_are_the_stalest_first(tmp_path):
    conn, cfg = db(tmp_path)
    jobs = explore.due(conn, cfg, NOW)
    assert jobs and jobs == sorted(jobs, key=lambda j: j.depart)
    t = FakeTransport([(200, None, REPLY)])
    explore.run(conn, cfg, jobs[:1], transport=t, now=NOW)
    after = explore.due(conn, cfg, NOW + timedelta(hours=1))
    assert jobs[0] not in after and len(after) == len(jobs) - 1
    assert jobs[0] in explore.due(conn, cfg, NOW + timedelta(hours=25))


# ---------------------------------------------------------------------------
# Reading a board back
# ---------------------------------------------------------------------------


def _price(conn, name, cents, depart, hours_ago=1, country="Japan", airport="NRT", nights=7):
    conn.execute(
        """INSERT INTO explore_prices (origin, region, place_id, name, country, airport,
               depart_date, nights, price_cents, observed_at)
           VALUES ('SEA', 'asia', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"/m/{name}", name, country, airport, depart, nights, cents,
         iso(NOW - timedelta(hours=hours_ago))),
    )  # fmt: skip
    conn.commit()


def test_a_board_shows_each_places_cheapest_current_fare(tmp_path):
    conn, cfg = db(tmp_path)
    jp = _watch(conn, "Japan", ["japan"], nights=7, max_price=70000)
    board = next(b for b in explore.boards(conn, cfg) if b.watch_id == jp.id)
    d1, d2, d3 = explore.board_departures(cfg, board, TODAY)[:3]
    _price(conn, "Tokyo", 80000, d1)
    _price(conn, "Tokyo", 65000, d2)
    _price(conn, "Tokyo", 50000, d3, hours_ago=5)  # superseded by a newer, higher fare
    _price(conn, "Tokyo", 90000, d3, hours_ago=1)
    _price(conn, "Osaka", 60000, d1, hours_ago=100)  # too old to show
    _price(conn, "Seoul", 40000, d1, country="South Korea", airport="ICN")  # not Japan
    _price(conn, "Sapporo", 75000, "2026-12-02")  # not one of the board's departures
    v = explore.view(conn, cfg, board, NOW)
    assert [(p.name, p.price_cents, p.depart_date) for p in v.places] == [("Tokyo", 65000, d2)]
    tokyo = v.places[0]
    assert (
        tokyo.under_max
        and tokyo.return_date == (date.fromisoformat(d2) + timedelta(days=7)).isoformat()
    )
    assert tokyo.link and "google.com/travel/flights" in tokyo.link
    assert v.priced_departures == 3 and v.departures >= 3


def test_the_explore_api_lists_the_boards(tmp_path):
    from fastapi.testclient import TestClient

    from flighttrack.api.app import create_app

    conn, cfg = db(tmp_path)
    explore.run(
        conn, cfg, [_job(_first_friday(cfg))], transport=FakeTransport([(200, None, REPLY)])
    )
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "t.db", env="dev")
    app = create_app(settings, cfg=cfg)
    with TestClient(app) as c:
        body = c.get("/api/explore").json()
    weekend = next(b for b in body if b["key"] == "anywhere-3")
    assert weekend["nights"] == 3 and weekend["region"] == "anywhere"
    assert [p["name"] for p in weekend["places"]][:2] == ["Dubai", "Mumbai"]
    assert weekend["places"][0]["price"] == 54800 and weekend["priced_departures"] == 1
    assert next(b for b in body if b["key"] == "anywhere-7")["places"] == []


def _first_friday(cfg):
    board = explore.Board("b", "t", "SEA", "anywhere", 3)
    return explore.board_departures(cfg, board, TODAY)[0]


# ---------------------------------------------------------------------------
# The worker's explore lane
# ---------------------------------------------------------------------------


def _worker(tmp_path, cfg, transport):
    w = Worker(
        Settings(data_dir=tmp_path / "data", db_path=tmp_path / "t.db"),
        cfg,
        source_factory=lambda c: FakeSource(),
        sleeper=lambda s: None,
        rng=random.Random(5),
        notifier=Notifier(),
    )
    w.transport = transport
    return w


def test_the_explore_lane_takes_its_turn_and_a_declined_reply_pauses_only_it(tmp_path, clock):
    conn, cfg = db(tmp_path)
    w = _worker(tmp_path, cfg, FakeTransport([(200, None, GATED)]))
    w.start()
    conn.executemany(
        "INSERT INTO fetch_attempts (at, source, outcome) VALUES (?, 'google_html', 'ok')",
        [(iso(NOW),)] * 10,
    )
    conn.commit()
    set_state(conn, STATE_NEXT_BATCH, iso(clock.t))
    assert w.tick() == "explore"
    assert pace.lane_paused(conn, "explore", clock.t) == iso(clock.t + timedelta(minutes=30))
    assert fetch.cooldown_active(conn, clock.t) is None
    set_state(conn, STATE_NEXT_BATCH, iso(clock.t))
    assert w.tick() == "batch"  # detail goes on


def test_explore_off_means_no_lane_and_no_boards(tmp_path, clock):
    conn, cfg = make_db(tmp_path, make_config(), today=TODAY)
    assert "explore" not in pace.lane_shares(cfg)
    w = _worker(tmp_path, cfg, FakeTransport([]))
    w.start()
    set_state(conn, STATE_NEXT_BATCH, iso(clock.t))
    assert w.tick() == "batch" and w.transport.calls == []


# ---------------------------------------------------------------------------
# Region watches keep their trip length and airport
# ---------------------------------------------------------------------------


def test_a_region_watch_keeps_its_trip_length_and_airport_through_export_and_import(tmp_path):
    conn, cfg = db(tmp_path)
    w = _watch(conn, "Europe", ["europe"], nights=14, origin="pdx")
    assert (w.nights, w.origin) == (14, "PDX")
    with pytest.raises(watches.WatchError):
        _watch(conn, "Bad", ["europe"], nights=40)
    exported = watches.export_watch_file(conn, cfg)
    entry = next(e for e in exported["deals"]["watches"] if e["name"] == "Europe")
    assert (entry["nights"], entry["origin"]) == (14, "PDX")
