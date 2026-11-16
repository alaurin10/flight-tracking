"""The three use cases from the brief, end to end through config → expand → alert."""

from datetime import date

import pytest
from helpers import add_obs, load_old, make_config, make_db

from flighttrack import alert, expand
from flighttrack.config import ConfigError, Pattern, Trip

TODAY = date(2026, 9, 17)


def test_ski_weekends_window_and_multiple_weekdays(tmp_path):
    """Scenario 2: every Fri→Mon (and Thu→Mon) from January through March, SLC only."""
    ski = Pattern(
        "ski",
        "FRI",
        3,
        depart_dows=("FRI", "THU"),
        routes=("SLC",),
        window_start="2027-01-01",
        window_end="2027-03-31",
    )
    cfg = make_config(patterns=[ski], max_days_ahead=60)  # horizon alone would end in November
    conn, cfg = make_db(tmp_path, cfg, today=TODAY)
    rows = conn.execute(
        "SELECT r.destination, q.depart_date, q.return_date FROM queries q JOIN routes r ON r.id "
        "= q.route_id WHERE q.active = 1"
    ).fetchall()
    assert {r["destination"] for r in rows} == {"SLC"}
    deps = sorted(r["depart_date"] for r in rows)
    assert deps[0] >= "2027-01-01" and deps[-1] <= "2027-03-31"
    assert {date.fromisoformat(d).weekday() for d in deps} == {3, 4}
    assert all(
        (date.fromisoformat(r["return_date"]) - date.fromisoformat(r["depart_date"])).days == 3
        for r in rows
    )
    assert len(deps) == 25  # 13 Fridays + 12 Thursdays, Jan 1 2027 is a Friday


def test_fixed_trip_tracks_up_to_departure_and_uses_its_own_target(tmp_path):
    """Scenario 1: dates are set; watch for the moment to book."""
    cfg = make_config(
        patterns=[Pattern("thu", "THU", 4)],
        trips=[Trip("japan", "HND", "2026-09-30", "2026-10-14", target_price=90000)],
        notify={"channel": "none"},
    )
    conn, cfg = make_db(tmp_path, cfg)
    res = expand.expand(conn, cfg, today=TODAY, build_links=False)
    assert res.trips == 1
    row = conn.execute(
        "SELECT id, pattern FROM queries WHERE depart_date = '2026-09-30'"
    ).fetchone()
    assert row["pattern"] == "japan"  # inside min_days_ahead=21, still tracked
    add_obs(conn, row["id"], 88000)  # under the trip target, over the route's $650
    stats = alert.run(conn, cfg, verbose=False)
    assert stats.qualified == 1
    assert conn.execute("SELECT reason FROM alerts_sent").fetchone()[0] == "threshold"


def test_trip_in_the_past_is_not_expanded(tmp_path):
    cfg = make_config(trips=[Trip("old", "HND", "2020-01-01", "2020-01-10")])
    conn, cfg = make_db(tmp_path, cfg)
    assert expand.expand(conn, cfg, today=TODAY, build_links=False).trips == 0


def test_window_reactivates_a_previously_expired_row(tmp_path):
    cfg = make_config(
        patterns=[Pattern("p", "FRI", 3, window_start="2027-01-01", window_end="2027-01-31")],
        max_days_ahead=400,
    )
    conn, cfg = make_db(tmp_path, cfg, today=TODAY)
    conn.execute("UPDATE queries SET active = 0")
    conn.commit()
    res = expand.expand(conn, cfg, today=TODAY, build_links=False)
    assert res.queries_reactivated > 0 and res.queries_inserted == 0


BASE = """
home: SEA
routes:
  - dest: HND
    label: Tokyo
  - dest: SLC
    label: Salt Lake City
notify:
  channel: none
"""


def _load(tmp_path, extra):
    p = tmp_path / "c.yaml"
    p.write_text(BASE + extra)
    return load_old(p, strict_secrets=False)


def test_config_scenario_blocks_parse(tmp_path):
    cfg = _load(
        tmp_path,
        """
patterns:
  - name: ski
    depart_dow: [FRI, THU]
    nights: 3
    routes: [SLC]
    window: {start: 2027-01-01, end: 2027-03-31}
    min_days_ahead: 7
trips:
  - name: japan
    dest: HND
    depart: 2027-04-10
    return: 2027-04-24
    target_price: 80000
calendar:
  enabled: true
deals:
  enabled: true
  feeds: [https://www.secretflying.com/feed/]
  watches:
    - name: Europe
      origins: [Seattle, SEA, West Coast]
      destinations: [europe]
      max_price: 60000
fetch:
  sources: [google_html]
  transport: urllib
  cooldown_hours: [2, 8]
""",
    )
    p = cfg.patterns[0]
    assert (
        p.depart_dows == ("FRI", "THU")
        and p.routes == ("SLC",)
        and p.window_end == "2027-03-31"
        and p.min_days_ahead == 7
    )
    assert cfg.trips[0].target_price == 80000 and cfg.trips[0].ret == "2027-04-24"
    assert cfg.calendar.enabled and cfg.deal_watches[0].destinations == ("europe",)
    assert cfg.fetch.sources == ("google_html",) and cfg.fetch.cooldown_hours == (2.0, 8.0)


@pytest.mark.parametrize(
    "extra, match",
    [
        (
            "trips:\n  - name: t\n    dest: LAX\n    depart: 2027-01-01\n",
            "must also be listed under routes",
        ),
        (
            "trips:\n  - name: t\n    dest: HND\n    depart: 2027-01-10\n    return: 2027-01-01\n",
            "return must be after",
        ),
        ("patterns:\n  - name: p\n    depart_dow: [FRI, FUNDAY]\n    nights: 3\n", "depart_dow"),
        (
            "patterns:\n  - name: p\n    depart_dow: FRI\n    nights: 3\n    routes: [LAX]\n",
            "not in routes",
        ),
        (
            "patterns:\n  - name: p\n    depart_dow: FRI\n    nights: 3\n    window: "
            "{start: 2027-02-01, end: 2027-01-01}\n",
            "start is after end",
        ),
        (
            "patterns:\n  - name: p\n    depart_dow: FRI\n    nights: 3\nfetch:\n  sources: "
            "[kayak]\n",
            "unknown source",
        ),
    ],
)
def test_config_rejects_bad_scenario_values(tmp_path, extra, match):
    with pytest.raises(ConfigError, match=match):
        _load(tmp_path, extra)


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def _shipped_tunables(tmp_path):
    from flighttrack.config import load
    from flighttrack.settings import REPO_ROOT, Settings

    settings = Settings(config_dir=REPO_ROOT / "config", data_dir=tmp_path)
    return load(strict_secrets=False, settings=settings)


def test_old_shipped_config_covers_all_three_scenarios(tmp_path):
    cfg = load_old(FIXTURES / "config_m1.yaml", strict_secrets=False)
    assert any(p.window_start for p in cfg.patterns), "a windowed (ski) pattern"
    assert cfg.trips, "a fixed trip"
    assert cfg.deal_watches, "a deal watch"


def test_shipped_tunables(tmp_path):
    cfg = _shipped_tunables(tmp_path)
    # M1: ntfy is gone, the dead and Europe-only feeds are gone, the cap is conservative.
    assert cfg.notify["channel"] == "file"
    assert cfg.deals.enabled
    assert cfg.deals.feeds == (
        "https://www.theflightdeal.com/feed/",
        "https://www.theflightdeal.com/category/flight-deals/seattle-flight-deals/feed/",
    )
    assert cfg.fetch.max_queries_per_run == 25
    assert cfg.db_path == str(tmp_path / "flights.db")
    assert cfg.failure_dir == str(tmp_path / "failures")
    assert cfg.notify["file"]["path"] == str(tmp_path / "out" / "digest.md")


def test_nothing_personal_is_shipped():
    """M3: the tunables file holds no watches, there is no config.yaml, and the seed
    example is a valid import file."""
    import yaml

    from flighttrack.config import read_watch_file
    from flighttrack.settings import REPO_ROOT

    tunables = yaml.safe_load((REPO_ROOT / "config" / "flighttrack.yaml").read_text())
    assert set(tunables) == {
        "search", "fetch", "calendar", "alerts", "health", "retention", "horizon", "notify",
        "deals", "collect", "explore", "finds",
    }  # fmt: skip
    assert set(tunables["deals"]) == {"enabled", "feeds", "max_alerts_per_run", "lookback_days"}
    assert not (REPO_ROOT / "config.yaml").exists()
    seed = read_watch_file(REPO_ROOT / "config" / "seed.example.yaml")
    assert seed.trips and seed.patterns and seed.deal_watches
