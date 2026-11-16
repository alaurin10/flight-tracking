"""Google answering with its own error instead of data is named for what it is (D1).

* A results page whose `ds:1` holds `[13, null, [[".../ErrorResponse", …]]]` was
  recorded as "no flights" (plan problem 13). It is a server error: retried once,
  never stored as empty, no cooldown.
* A calendar reply with no prices that is an error envelope or almost empty is
  `gated`: Google declining, not a layout change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from helpers import NO_RESULTS_HTML, FakeClock, FakeTransport, make_config, make_db

from flighttrack import fetch, health, pace, sweep
from flighttrack import source as src
from flighttrack.config import Calendar
from flighttrack.gflights.calendar import looks_gated, parse_calendar
from flighttrack.gflights.page import PageKind, classify
from flighttrack.gflights.transport import Response
from flighttrack.source import FakeSource, TransientError
from flighttrack.store.db import set_clock

NOW = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "live-20261115" / "results_error13_sea_slc_rt.html"


@pytest.fixture(autouse=True)
def clock():
    c = FakeClock(NOW)
    set_clock(c)
    return c


def _resp(text: str, status: int = 200) -> Response:
    return Response(status, "https://www.google.com/travel/flights/search", text, 0.0, "f", {})


# ---------------------------------------------------------------------------
# The results page
# ---------------------------------------------------------------------------


def test_the_error_13_page_is_a_server_error_not_no_results():
    page = classify(_resp(FIXTURE.read_text(encoding="utf-8")))
    assert page.kind is PageKind.SERVER_ERROR
    assert "error 13" in page.detail
    assert classify(_resp(NO_RESULTS_HTML)).kind is PageKind.NO_RESULTS


def test_the_html_source_raises_a_transient_error_and_the_chain_stops_there():
    page = FIXTURE.read_text(encoding="utf-8")
    html = src.GoogleHtmlSource(transport=FakeTransport([(200, None, page)]))
    cfg = make_config()
    route = cfg.route("SLC")
    with pytest.raises(TransientError):
        html.fetch(cfg, route, "2027-01-13", "2027-01-16", 5)

    class Second:
        name = "second"
        called = False

        def fetch(self, *a):
            Second.called = True
            return []

    chain = src.ChainSource(
        [src.GoogleHtmlSource(transport=FakeTransport([(200, None, page)])), Second()]
    )
    with pytest.raises(TransientError):
        chain.fetch(cfg, route, "2027-01-13", "2027-01-16", 5)
    assert not Second.called  # the next parser would only fetch the same page again


class ErrorsFirst(FakeSource):
    """Google's error for the first `n` requests of each date pair, then prices."""

    def __init__(self, n: int, keys=None):
        super().__init__()
        self.n, self.keys, self.seen = n, keys, {}

    def fetch(self, cfg, route, depart, ret, limit):
        key = (route.dest, depart, ret)
        self.seen[key] = self.seen.get(key, 0) + 1
        if (self.keys is None or key in self.keys) and self.seen[key] <= self.n:
            self.calls.append(key)
            raise TransientError("Google returned error 13 instead of results")
        return super().fetch(cfg, route, depart, ret, limit)


def test_error_13_is_retried_once_and_then_stored_normally(tmp_path):
    conn, cfg = make_db(tmp_path, today=NOW.date())
    source = ErrorsFirst(1)
    stats = fetch.run(conn, cfg, source, now=NOW, limit=2, verbose=False, sleeper=lambda s: None)
    assert stats.succeeded == 2 and stats.retries == 2 and stats.failed == 0
    assert len(source.calls) == 4
    outcomes = [r[0] for r in conn.execute("SELECT outcome FROM fetch_attempts ORDER BY id")]
    assert outcomes == ["google_error", "ok", "google_error", "ok"]
    assert fetch.cooldown_active(conn, NOW) is None


def test_error_13_twice_leaves_the_pair_due_without_a_cooldown(tmp_path, clock):
    conn, cfg = make_db(tmp_path, today=NOW.date())
    first = fetch.select_due(conn, cfg, NOW, limit=1)[0]
    key = (first.route.dest, first.depart_date, first.return_date)
    source = ErrorsFirst(2, keys={key})
    stats = fetch.run(conn, cfg, source, now=NOW, limit=3, verbose=False, sleeper=lambda s: None)
    assert stats.by_kind["google_error"] == 1 and stats.succeeded == 2
    assert not stats.blocked and fetch.cooldown_active(conn, NOW) is None
    row = conn.execute("SELECT last_fetch_at FROM queries WHERE id = ?", (first.query_id,))
    assert row.fetchone()[0] is None  # not "fetched, no flights"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM observations WHERE query_id = ?", (first.query_id,)
        ).fetchone()[0]
        == 0
    )
    # It waits an hour, so one bad date cannot take every slot; then it is due again.
    soon = [d.query_id for d in fetch.select_due(conn, cfg, NOW + timedelta(minutes=10), limit=99)]
    assert first.query_id not in soon
    later = fetch.select_due(conn, cfg, NOW + timedelta(minutes=61), limit=99)
    assert first.query_id in [d.query_id for d in later]


def test_three_error_13s_in_a_row_end_the_batch_without_a_cooldown(tmp_path):
    conn, cfg = make_db(tmp_path, today=NOW.date())
    source = ErrorsFirst(99)
    stats = fetch.run(conn, cfg, source, now=NOW, limit=5, verbose=False, sleeper=lambda s: None)
    assert stats.attempted == 3 and stats.abort_reason == "google_error"
    assert not stats.blocked and fetch.cooldown_active(conn, NOW) is None
    assert pace.allowance(conn, cfg) == cfg.collect.pace_start_per_day  # not a block


# ---------------------------------------------------------------------------
# The calendar
# ---------------------------------------------------------------------------

GATED = ')]}\'\n\n[["wrb.fr","GetCalendarGraph",null,null,null,[13],"generic"]]'


def test_the_captured_empty_calendar_reply_is_gated():
    live = Path(__file__).parent / "fixtures" / "live-20261116" / "calendar_gated_sea_slc_3n.txt"
    text = live.read_text(encoding="utf-8")
    assert len(text) == 363
    assert parse_calendar(text) == [] and looks_gated(text)
    stripped = text.replace("ErrorResponse", "Something") + " " * 2000
    assert looks_gated(stripped)  # the payload-less entry alone is enough


def test_looks_gated():
    assert looks_gated(GATED)
    assert looks_gated(")]}'\n" + " " * 363)
    assert looks_gated("x" * 5000 + "ErrorResponse")
    assert not looks_gated("<html>" + "nothing " * 500 + "</html>")


def test_a_gated_calendar_reply_stops_the_run_and_is_logged_as_gated(tmp_path):
    cfg = make_config(calendar=Calendar(enabled=True, max_calls_per_run=3))
    conn, cfg = make_db(tmp_path, cfg, today=NOW.date())
    t = FakeTransport([(200, None, GATED)] * 3)
    stats = sweep.run(conn, cfg, transport=t, now=NOW, sleeper=lambda s: None, verbose=False)
    assert stats.gated and not stats.blocked and stats.calls == 1 and len(t.calls) == 1
    assert [r[0] for r in conn.execute("SELECT outcome FROM fetch_attempts")] == ["gated"]
    assert fetch.cooldown_active(conn, NOW) is None
    scan = conn.execute("SELECT last_outcome, last_run_at, fail_streak FROM scans").fetchone()
    assert tuple(scan) == ("gated", None, 1)


def test_health_warns_when_the_calendar_lane_has_been_paused_a_day(tmp_path, clock):
    cfg = make_config(calendar=Calendar(enabled=True))
    conn, cfg = make_db(tmp_path, cfg, today=NOW.date())
    pace.pause_lane(conn, "calendar", "declined", NOW - timedelta(hours=25))
    for _ in range(4):
        pace.pause_lane(conn, "calendar", "declined", NOW - timedelta(hours=1))
    rep = health.check(conn, cfg, NOW)
    assert "calendar_gated" in [p.code for p in rep.problems]
    assert any(line.startswith("pace 50 requests a day") for line in rep.info)
    pace.clear_lane(conn, "calendar")
    assert "calendar_gated" not in [p.code for p in health.check(conn, cfg, NOW).problems]
