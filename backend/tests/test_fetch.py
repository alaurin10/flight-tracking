"""Fetcher: pacing, the query cap, rotation, and abort-on-block."""

from datetime import datetime, timedelta, timezone

from flighttrack import fetch
from flighttrack.source import FakeSource, NoResults, SourceError
from helpers import make_config, make_db


class Boom:
    """Always fails, as a rate limiter would."""

    def __init__(self):
        self.calls = 0

    def fetch(self, *a, **k):
        self.calls += 1
        raise SourceError("429 rate limited")


class Empty:
    """Always answers, with nothing. Proof we are NOT blocked."""

    def __init__(self):
        self.calls = 0

    def fetch(self, *a, **k):
        self.calls += 1
        raise NoResults("no inventory")


def _run(conn, cfg, source, **kw):
    return fetch.run(conn, cfg, source, sleeper=lambda s: None, verbose=False, **kw)


def test_respects_the_query_cap(tmp_path):
    cfg = make_config()
    conn, cfg = make_db(tmp_path, cfg)
    src = FakeSource()
    stats = _run(conn, cfg, src)
    assert stats.attempted == cfg.fetch.max_queries_per_run == 5
    assert len(src.calls) == 5


def test_stores_all_offers_with_one_best(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    _run(conn, cfg, FakeSource(default=[30000, 32000, 35000]), limit=1)
    rows = conn.execute(
        "SELECT price_cents, is_best FROM observations ORDER BY price_cents"
    ).fetchall()
    assert [r[0] for r in rows] == [30000, 32000, 35000]
    assert [r[1] for r in rows] == [1, 0, 0]  # cheapest flagged, runners-up kept


def test_second_run_rotates_to_different_queries(tmp_path):
    """The grid is larger than one run's cap, so no query may starve."""
    conn, cfg = make_db(tmp_path, make_config())
    first, second = FakeSource(), FakeSource()
    _run(conn, cfg, first)
    _run(conn, cfg, second)
    assert set(first.calls).isdisjoint(set(second.calls))


def test_priority_one_routes_go_first(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    src = FakeSource()
    _run(conn, cfg, src)
    # HND and SLC are priority 1; NRT is priority 2 and should be untouched.
    assert "NRT" not in {dest for dest, _, _ in src.calls}


def test_recently_fetched_queries_are_not_due(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    _run(conn, cfg, FakeSource())
    # Immediately re-selecting must skip everything just fetched.
    due = fetch.select_due(conn, cfg)
    assert all(d.last_fetch_at is None for d in due)


def test_priority_interval_gates_refetch(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    _run(conn, cfg, FakeSource())
    fetched = conn.execute(
        "SELECT COUNT(*) FROM queries WHERE last_fetch_at IS NOT NULL"
    ).fetchone()[0]
    assert fetched == 5

    # A day later, the priority-1 rows are due again.
    later = datetime.now(timezone.utc) + timedelta(days=1, minutes=1)
    due = fetch.select_due(conn, cfg, now=later, limit=100)
    assert any(d.last_fetch_at is not None for d in due)


def test_aborts_after_consecutive_failures(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    boom = Boom()
    stats = _run(conn, cfg, boom, limit=5)

    assert stats.blocked is True
    assert boom.calls == cfg.fetch.abort_after_consecutive_failures == 2
    row = conn.execute("SELECT blocked, notes FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["blocked"] == 1 and "consecutive failures" in row["notes"]


def test_empty_results_do_not_count_as_blocking(tmp_path):
    """An empty answer proves the request got through — it must not abort the run."""
    conn, cfg = make_db(tmp_path, make_config())
    empty = Empty()
    stats = _run(conn, cfg, empty, limit=5)

    assert stats.blocked is False
    assert stats.empty == 5 and stats.failed == 0
    assert empty.calls == 5


def test_empty_result_still_marks_query_fetched(tmp_path):
    """Otherwise a route with no inventory gets re-hammered every run."""
    conn, cfg = make_db(tmp_path, make_config())
    _run(conn, cfg, Empty(), limit=3)
    assert conn.execute(
        "SELECT COUNT(*) FROM queries WHERE last_fetch_at IS NOT NULL"
    ).fetchone()[0] == 3


def test_failure_then_success_resets_the_counter(tmp_path):
    """One transient failure must not arm the abort for the rest of the run."""

    class Flaky:
        def __init__(self):
            self.n = 0

        def fetch(self, cfg, route, depart, ret, limit):
            self.n += 1
            if self.n % 2 == 1:
                raise SourceError("transient")
            return FakeSource().fetch(cfg, route, depart, ret, limit)

    conn, cfg = make_db(tmp_path, make_config())
    stats = _run(conn, cfg, Flaky(), limit=6)
    assert stats.blocked is False
    assert stats.succeeded == 3 and stats.failed == 3


def test_run_log_records_every_run(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    _run(conn, cfg, FakeSource(), limit=2)
    row = conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["finished_at"] is not None
    assert row["attempted"] == 2 and row["succeeded"] == 2


def test_implausible_prices_are_stored_and_flagged(tmp_path):
    """Never drop data — but make a units change impossible to miss."""
    conn, cfg = make_db(tmp_path, make_config())
    stats = _run(conn, cfg, FakeSource(default=[5]), limit=1)
    assert stats.implausible == 1
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    note = conn.execute("SELECT notes FROM run_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "plausible price band" in note


def test_sleep_is_jittered_between_calls_only(tmp_path):
    conn, cfg = make_db(tmp_path, make_config(
        fetch=make_config().fetch.__class__(max_queries_per_run=4, min_sleep_seconds=4.0,
                                           max_sleep_seconds=8.0)))
    slept = []
    fetch.run(conn, cfg, FakeSource(), sleeper=slept.append, verbose=False)
    assert len(slept) == 3  # n-1 sleeps for n calls
    assert all(4.0 <= s <= 8.0 for s in slept)
    assert len(set(slept)) > 1  # jittered, not a fixed interval


def test_error_text_is_collapsed_for_the_log(tmp_path):
    """A multi-line proxy/HTTP failure must not turn run_log into a wall of headers."""
    from flighttrack.source import tidy_error

    messy = "proxy CONNECT failed: HTTP/1.1 403 Forbidden\nContent-Type: text/plain\n\nConnection: close"
    tidied = tidy_error(messy)
    assert "\n" not in tidied
    assert tidied.startswith("proxy CONNECT failed: HTTP/1.1 403 Forbidden Content-Type")

    assert len(tidy_error("x" * 5000)) <= 300
    assert tidy_error("x" * 5000).endswith("…")
