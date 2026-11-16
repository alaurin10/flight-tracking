"""The deal finder (D3): usual prices, candidates from the Explore boards, confirmation on
the results page, rechecks and alerts, the worker's detail slots, and the API. Offline:
fares are written straight into explore_prices and FakeSource answers the checks."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from helpers import FakeClock, make_config, make_db

from flighttrack import explore, fetch, finds, watches
from flighttrack.config import Explore, Fetch, Finds
from flighttrack.notifier import Notifier
from flighttrack.scheduler import STATE_NEXT_BATCH, Worker
from flighttrack.settings import Settings
from flighttrack.source import FakeSource
from flighttrack.store.db import iso, set_clock, set_state

NOW = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
TODAY = NOW.date()


@pytest.fixture(autouse=True)
def clock():
    c = FakeClock(NOW)
    set_clock(c)
    return c


def db(tmp_path, **over):
    settings = {
        "explore": Explore(enabled=True, boards=(7,)),
        "finds": Finds(enabled=True),
        "failure_dir": str(tmp_path / "failures"),
        "fetch": Fetch(max_queries_per_run=5, min_sleep_seconds=0, max_sleep_seconds=0),
        "max_days_ahead": 90,
    }
    cfg = make_config(**{**settings, **over})
    return make_db(tmp_path, cfg, today=TODAY)


def board(conn, cfg, key="anywhere-7"):
    return next(b for b in explore.boards(conn, cfg) if b.key == key)


def fare(conn, b, name, airport, cents, depart, when, place=None):
    conn.execute(
        """INSERT INTO explore_prices (origin, region, place_id, name, country, airport,
               depart_date, nights, price_cents, airline, stops, duration_min, observed_at)
           VALUES (?, ?, ?, ?, 'Somewhere', ?, ?, ?, ?, 'Alaska', 0, 300, ?)""",
        (b.origin, b.region, place or f"/m/{airport.lower()}", name, airport, depart, b.nights,
         cents, iso(when)),
    )  # fmt: skip
    conn.commit()


def history(conn, b, name, airport, depart, prices, days_ago_start=10):
    """One fare a day, ending `days_ago_start - len(prices) + 1` days ago."""
    for i, cents in enumerate(prices):
        fare(conn, b, name, airport, cents, depart, NOW - timedelta(days=days_ago_start - i))


def find_rows(conn):
    return {r["airport"]: r for r in conn.execute("SELECT * FROM finds")}


# ---------------------------------------------------------------------------
# The usual price
# ---------------------------------------------------------------------------


def test_the_usual_price_is_the_median_daily_low_of_that_month_and_trip_length(tmp_path):
    conn, cfg = db(tmp_path)
    b = board(conn, cfg)
    d = explore.board_departures(cfg, b, TODAY)[0]
    history(conn, b, "Lisbon", "LIS", d, [60000, 62000, 58000, 61000, 59000, 63000])
    u = finds.usual_price(conn, cfg, "SEA", "/m/lis", 7, int(d[5:7]), iso(NOW), NOW)
    assert u.cents is None and u.lowest == 58000 and u.days == 6  # 6 days < 7
    fare(conn, b, "Lisbon", "LIS", 30000, d, NOW - timedelta(days=4))  # a 7th day
    fare(conn, b, "Lisbon", "LIS", 99000, d, NOW - timedelta(days=4, hours=-1))  # same day
    u = finds.usual_price(conn, cfg, "SEA", "/m/lis", 7, int(d[5:7]), iso(NOW), NOW)
    assert u.days == 7 and u.cents == 60000 and u.lowest == 30000
    # Only fares seen before `before` count, and only this trip length.
    early = iso(NOW - timedelta(days=8))
    assert finds.usual_price(conn, cfg, "SEA", "/m/lis", 7, int(d[5:7]), early, NOW).days == 2
    assert finds.usual_price(conn, cfg, "SEA", "/m/lis", 3, int(d[5:7]), iso(NOW), NOW).days == 0


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def test_a_fare_well_under_its_usual_price_becomes_a_candidate(tmp_path):
    conn, cfg = db(tmp_path, finds=Finds(enabled=True, cheapest_per_board=0))
    b = board(conn, cfg)
    d = explore.board_departures(cfg, b, TODAY)[0]
    history(conn, b, "Lisbon", "LIS", d, [60000] * 8)
    history(conn, b, "Denver", "DEN", d, [20000] * 7 + [18000])  # lowest seen $180
    fare(conn, b, "Lisbon", "LIS", 42000, d, NOW - timedelta(hours=1))  # 30% under
    fare(conn, b, "Denver", "DEN", 17000, d, NOW - timedelta(hours=1))  # 15% under, 6% new low
    assert finds.find_candidates(conn, cfg, NOW) == 1
    lis = find_rows(conn)["LIS"]
    assert lis["status"] == "candidate" and lis["estimate_cents"] == 42000
    assert lis["usual_cents"] == 60000 and lis["why"] == "30% under its usual $600"
    assert lis["key"] == f"SEA|LIS|{d}|7" and lis["board_key"] == "anywhere-7"
    assert finds.find_candidates(conn, cfg, NOW) == 0  # not raised twice


def test_under_a_watchs_price_a_new_low_and_while_learning_the_cheapest_few(tmp_path):
    conn, cfg = db(tmp_path, finds=Finds(enabled=True, cheapest_per_board=2))
    w = watches.create_deal_watch(
        conn,
        {"name": "Japan", "origins": ["Seattle"], "destinations": ["japan"], "nights": 7,
         "max_price": 70000},
    )  # fmt: skip
    jp = board(conn, cfg, f"watch-{w.id}")
    any7 = board(conn, cfg)
    d = explore.board_departures(cfg, any7, TODAY)[0]
    fare(conn, jp, "Tokyo", "HND", 68000, d, NOW - timedelta(hours=1), place="/m/tokyo")
    history(conn, any7, "Reno", "RNO", d, [30000, 31000, 32000])  # 3 days, lowest $300
    fare(conn, any7, "Reno", "RNO", 26000, d, NOW - timedelta(hours=1))  # 13% under it
    fare(conn, any7, "Boise", "BOI", 9000, d, NOW - timedelta(hours=1))  # no history: cheapest
    fare(conn, any7, "Omaha", "OMA", 99000, d, NOW - timedelta(hours=1))  # no history, 3rd
    finds.find_candidates(conn, cfg, NOW)
    rows = find_rows(conn)
    assert rows["HND"]["why"] == "under your $700" and rows["HND"]["max_cents"] == 70000
    assert rows["RNO"]["why"].startswith("13% under the lowest seen before ($300)")
    assert rows["BOI"]["why"].startswith("one of the cheapest on “Anywhere from SEA · a week”")
    assert "OMA" not in rows  # third place on the board, past cheapest_per_board


def test_a_gone_find_comes_back_only_when_cheaper_or_after_a_week(tmp_path, clock):
    conn, cfg = db(tmp_path, finds=Finds(enabled=True, cheapest_per_board=1))
    b = board(conn, cfg)
    d = explore.board_departures(cfg, b, TODAY)[0]
    fare(conn, b, "Boise", "BOI", 20000, d, NOW - timedelta(hours=1))
    finds.find_candidates(conn, cfg, NOW)
    conn.execute("UPDATE finds SET status = 'gone'")
    conn.commit()
    clock.advance(hours=2)
    fare(conn, b, "Boise", "BOI", 19000, d, clock.t - timedelta(minutes=5))  # only 5% less
    assert finds.find_candidates(conn, cfg, clock.t) == 0
    fare(conn, b, "Boise", "BOI", 17000, d, clock.t - timedelta(minutes=1))  # 15% less
    assert finds.find_candidates(conn, cfg, clock.t) == 1
    row = find_rows(conn)["BOI"]
    assert row["status"] == "candidate" and row["estimate_cents"] == 17000


# ---------------------------------------------------------------------------
# Checking on the results page
# ---------------------------------------------------------------------------


def candidate(conn, cfg, airport="LIS", cents=42000, usual=60000, max_cents=None, nights=7):
    d = explore.board_departures(cfg, board(conn, cfg), TODAY)[0]
    conn.execute(
        """INSERT INTO finds (key, origin, airport, depart_date, nights, name, estimate_cents,
               usual_cents, why, max_cents, found_at)
           VALUES (?, 'SEA', ?, ?, ?, ?, ?, ?, '30% under its usual $600', ?, ?)""",
        (finds.find_key("SEA", airport, d, nights), airport, d, nights, f"Place {airport}",
         cents, usual, max_cents, iso(NOW)),
    )  # fmt: skip
    conn.commit()
    return d


def check_all(conn, cfg, source, now=NOW):
    return finds.check(conn, cfg, source, finds.due(conn, cfg, now), Notifier(), now=now,
                       sleeper=lambda s: None)  # fmt: skip


def test_a_candidate_confirmed_on_the_results_page_alerts_once(tmp_path):
    conn, cfg = db(tmp_path)
    d = candidate(conn, cfg)
    ret = finds.return_date(d, 7)
    src = FakeSource({("LIS", d, ret): [52000, 61000]})  # within 30% of $420 ($546)
    stats = check_all(conn, cfg, src)
    assert (stats.requests, stats.confirmed, stats.notified) == (1, 1, 1)
    row = find_rows(conn)["LIS"]
    assert row["status"] == "confirmed" and row["price_cents"] == 52000
    assert row["notified_cents"] == 52000 and row["confirmed_at"] == iso(NOW)
    note = conn.execute("SELECT kind, title, url FROM notifications").fetchone()
    assert note["kind"] == "deal" and note["title"].startswith("✈ $520 Place LIS")
    assert note["url"] == "/deals"
    att = conn.execute("SELECT source, outcome, query_id FROM fetch_attempts").fetchone()
    assert tuple(att) == ("fake", "ok", None)


def test_rechecks_alert_again_on_a_further_drop_and_close_when_it_rises(tmp_path, clock):
    conn, cfg = db(tmp_path)
    d = candidate(conn, cfg)
    ret = finds.return_date(d, 7)
    src = FakeSource({("LIS", d, ret): [52000]})
    check_all(conn, cfg, src)
    assert finds.due(conn, cfg, NOW + timedelta(hours=11)) == []
    clock.advance(hours=13)
    src.prices[("LIS", d, ret)] = [50000]  # 4% lower: no second alert
    check_all(conn, cfg, src, clock.t)
    clock.advance(hours=13)
    src.prices[("LIS", d, ret)] = [45000]  # 13% under the alerted $520
    check_all(conn, cfg, src, clock.t)
    titles = [r[0] for r in conn.execute("SELECT title FROM notifications ORDER BY id")]
    assert len(titles) == 2 and titles[1].startswith("✈ $450")
    clock.advance(hours=13)
    src.prices[("LIS", d, ret)] = [70000]  # past $546
    check_all(conn, cfg, src, clock.t)
    row = find_rows(conn)["LIS"]
    assert row["status"] == "gone" and "the real fare" in row["note"] and "$700" in row["note"]


def test_a_real_fare_far_over_the_estimate_is_turned_down_unless_under_the_watchs_price(tmp_path):
    conn, cfg = db(tmp_path)
    d = candidate(conn, cfg, "LIS", cents=42000)
    candidate(conn, cfg, "HND", cents=60000, max_cents=90000)
    ret = finds.return_date(d, 7)
    src = FakeSource({("LIS", d, ret): [60000], ("HND", d, ret): [85000]})
    check_all(conn, cfg, src)
    rows = find_rows(conn)
    assert rows["LIS"]["status"] == "gone"  # $600 > $546
    assert rows["HND"]["status"] == "confirmed"  # $850 > $780 but under the $900 asked for


def test_no_flights_a_block_and_repeated_trouble(tmp_path):
    conn, cfg = db(tmp_path)
    d = candidate(conn, cfg, "LIS")
    candidate(conn, cfg, "HND")
    ret = finds.return_date(d, 7)
    src = FakeSource({("LIS", d, ret): [], ("HND", d, ret): "blocked"})
    stats = check_all(conn, cfg, src)
    rows = find_rows(conn)
    assert rows["LIS"]["status"] == "gone" and "no flights" in rows["LIS"]["note"]
    assert stats.blocked and fetch.cooldown_active(conn, NOW)
    assert rows["HND"]["status"] == "candidate"
    fetch.clear_cooldown(conn)
    src.prices[("HND", d, ret)] = "layout"
    for _ in range(3):
        check_all(conn, cfg, src)
    row = find_rows(conn)["HND"]
    assert row["status"] == "gone" and row["note"] == "could not be checked: layout"


def test_finds_whose_date_has_come_are_closed(tmp_path, clock):
    conn, cfg = db(tmp_path)
    d = candidate(conn, cfg)
    assert len(finds.due(conn, cfg, NOW)) == 1
    clock.set(datetime.fromisoformat(d + "T12:00:00+00:00"))
    assert finds.due(conn, cfg, clock.t) == []
    assert find_rows(conn)["LIS"]["note"] == "the date has passed"


# ---------------------------------------------------------------------------
# The worker and the API
# ---------------------------------------------------------------------------


def test_finds_go_first_in_a_detail_slot_after_dates_a_person_asked_for(tmp_path, clock):
    conn, cfg = db(tmp_path)
    cfg = make_config(**{**cfg.__dict__, "explore": Explore(enabled=False)})  # detail only
    d = candidate(conn, cfg)
    ret = finds.return_date(d, 7)
    src = FakeSource({("LIS", d, ret): [50000]})
    w = Worker(
        Settings(data_dir=tmp_path / "data", db_path=tmp_path / "t.db"),
        cfg,
        source_factory=lambda c: src,
        sleeper=lambda s: None,
        rng=random.Random(2),
        notifier=Notifier(),
    )
    w.start()
    asked = fetch.select_due(conn, cfg, NOW, limit=1)[0].query_id
    fetch.request_queries(conn, [asked], NOW)
    set_state(conn, STATE_NEXT_BATCH, iso(clock.t))
    assert w.tick() == "batch"
    assert ("LIS", d, ret) not in src.calls  # a person's dates first, finds wait
    clock.advance(minutes=1)
    set_state(conn, STATE_NEXT_BATCH, iso(clock.t))
    assert w.tick() == "batch"
    first = len(src.calls) - cfg.collect.batch_size
    assert src.calls[first] == ("LIS", d, ret)  # then the find, then dates
    assert find_rows(conn)["LIS"]["status"] == "confirmed"


def test_the_finds_api_and_the_overview(tmp_path):
    from fastapi.testclient import TestClient

    from flighttrack.api.app import create_app

    conn, cfg = db(tmp_path)
    d = candidate(conn, cfg, "LIS")
    candidate(conn, cfg, "HND", cents=60000, usual=90000)
    ret = finds.return_date(d, 7)
    check_all(conn, cfg, FakeSource({("LIS", d, ret): [45000], ("HND", d, ret): "network"}))
    app = create_app(Settings(data_dir=tmp_path / "data", db_path=tmp_path / "t.db"), cfg=cfg)
    with TestClient(app) as c:
        body = c.get("/api/finds").json()
        overview = c.get("/api/overview").json()
    assert [(f["airport"], f["status"]) for f in body] == [
        ("LIS", "confirmed"),
        ("HND", "candidate"),
    ]
    lis = body[0]
    assert (lis["price"], lis["estimate"], lis["usual"], lis["return_date"]) == (
        45000,
        42000,
        60000,
        ret,
    )
    assert "google.com/travel/flights" in lis["link"]
    assert [f["airport"] for f in overview["finds"]] == ["LIS"]


def test_finds_off_means_nothing_found_and_nothing_checked(tmp_path):
    conn, cfg = make_db(tmp_path, make_config(), today=TODAY)
    assert finds.find_candidates(conn, cfg, NOW) == 0 and finds.due(conn, cfg, NOW) == []
