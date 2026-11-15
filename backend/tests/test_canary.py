"""The daily canary: a known-busy search judged against what a results page must hold."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import (
    FakeClock,
    FakeTransport,
    make_config,
    make_db,
    make_itinerary,
    make_payload,
    make_results_html,
)

from flighttrack import canary, fetch, health
from flighttrack.config import Collect
from flighttrack.store.db import get_state, set_clock

NOW = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
LIVE = Path(__file__).parent / "fixtures" / "live-20261114" / "results_sea_slc_rt.html"
URL = "https://www.google.com/travel/flights/search"


@pytest.fixture
def db(tmp_path):
    set_clock(FakeClock(NOW))
    cfg = make_config(collect=Collect(canary_dest="LAX"), failure_dir=str(tmp_path / "fail"))
    return make_db(tmp_path, cfg)


def run(conn, cfg, *responses):
    t = FakeTransport(list(responses))
    return canary.run(conn, cfg, transport=t, now=NOW), t


def crit_codes(conn, cfg):
    return {p.code for p in health.check(conn, cfg, NOW).problems if p.severity == "crit"}


def test_the_real_page_passes(db):
    conn, cfg = db
    res, t = run(conn, cfg, (200, URL, LIVE.read_text(encoding="utf-8")))
    assert res.result == "pass" and res.itineraries == 12 and res.reasons == []
    # One request: home airport to the canary destination, one way, 30 days out.
    assert len(t.calls) == 1
    row = conn.execute("SELECT source, outcome FROM fetch_attempts").fetchone()
    assert tuple(row) == ("canary", "ok")
    assert fetch.budget_used(conn, NOW) == 1  # it spends the budget
    assert json.loads(get_state(conn, canary.STATE_LAST_CANARY))["result"] == "pass"


def test_failing_raises_a_critical_problem_and_passing_clears_it(db, tmp_path):
    """Acceptance: the canary failing raises a critical health problem; passing clears it."""
    conn, cfg = db
    thin = make_results_html(make_payload(best=[make_itinerary(300)], other=[]))
    res, _ = run(conn, cfg, (200, URL, thin))
    assert res.result == "fail"
    assert any("only 1 itineraries" in r for r in res.reasons)
    assert any("payload[25]" in r for r in res.reasons)
    assert res.saved and Path(res.saved).exists()  # the page is kept for diagnosis
    assert "layout_drift" in crit_codes(conn, cfg)
    assert not health.check(conn, cfg, NOW).ok

    res, _ = run(conn, cfg, (200, URL, LIVE.read_text(encoding="utf-8")))
    assert res.passed
    assert "layout_drift" not in crit_codes(conn, cfg)
    assert get_state(conn, health.STATE_LAYOUT_DRIFT) is None


def test_a_block_is_inconclusive_and_starts_the_cooldown(db):
    conn, cfg = db
    run(conn, cfg, (200, URL, make_results_html(make_payload(best=[], other=[]))))
    drift = get_state(conn, health.STATE_LAYOUT_DRIFT)
    assert drift is not None
    res, _ = run(conn, cfg, (429, "https://www.google.com/sorry/index", "no"))
    assert res.result == "inconclusive"
    assert get_state(conn, health.STATE_LAYOUT_DRIFT) == drift  # verdict unchanged
    assert fetch.cooldown_active(conn, NOW) is not None


def test_a_no_results_page_fails(db):
    conn, cfg = db
    from helpers import NO_RESULTS_HTML

    res, _ = run(conn, cfg, (200, URL, NO_RESULTS_HTML))
    assert res.result == "fail" and "no_results" in res.reasons[0]


def test_judge_names_each_problem():
    rows = [make_itinerary(300 + i, segments=[]) for i in range(6)]
    payload = make_payload(best=rows, other=None) + [None] * 18
    payload[25] = [[None, 250]]
    reasons, n = canary.judge(payload)
    text = " | ".join(reasons)
    assert n == 6
    assert "other departing flights list" in text
    assert "the page says the cheapest is 250, we read 300" in text
    assert "0 of 6 itineraries have an airline, times and a duration" in text
