"""Reporting and the static HTML page."""

from flighttrack import html, report
from helpers import add_obs, first_query_id, make_config, make_db


def test_grid_shows_current_and_trailing_low(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    qid = first_query_id(conn, "SLC")
    add_obs(conn, qid, 25000, days_ago=20)
    add_obs(conn, qid, 18000, days_ago=10)   # the 30-day low
    add_obs(conn, qid, 21000, days_ago=0)    # current

    row = [r for r in report.grid(conn, dest="SLC") if r.query_id == qid][0]
    assert row.current_cents == 21000
    assert row.low_cents == 18000
    assert round(row.vs_low_pct) == 17
    assert not row.is_at_low


def test_trailing_window_excludes_older_lows(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    qid = first_query_id(conn, "SLC")
    add_obs(conn, qid, 9000, days_ago=90)    # ancient bargain, outside the window
    add_obs(conn, qid, 21000, days_ago=0)

    row = [r for r in report.grid(conn, dest="SLC", window_days=30) if r.query_id == qid][0]
    assert row.low_cents == 21000
    assert row.is_at_low


def test_only_best_offers_form_the_series(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    qid = first_query_id(conn, "SLC")
    add_obs(conn, qid, 20000, is_best=1)
    add_obs(conn, qid, 26000, is_best=0)  # a runner-up from the same fetch

    assert [p for _, p in report.history(conn, qid)] == [20000]


def test_unpriced_pairs_are_hidden_by_default(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    assert report.grid(conn) == []
    assert len(report.grid(conn, include_unpriced=True)) > 0


def test_cheapest_sorts_across_routes(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    add_obs(conn, first_query_id(conn, "HND"), 80000)
    add_obs(conn, first_query_id(conn, "SLC"), 15000)
    add_obs(conn, first_query_id(conn, "NRT"), 70000)

    rows = report.cheapest(conn, n=3)
    assert [r.dest for r in rows] == ["SLC", "NRT", "HND"]


def test_sparkline_shapes():
    assert report.sparkline([]) == ""
    # A flat series has no range, so every point renders at the mid band.
    flat = report.sparkline([5, 5, 5])
    assert len(flat) == 3 and len(set(flat)) == 1
    assert flat[0] == report.SPARK[len(report.SPARK) // 2]

    rising = report.sparkline([1, 2, 3])
    assert rising[0] == report.SPARK[0] and rising[-1] == report.SPARK[-1]


def test_render_grid_marks_low_and_target(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    qid = first_query_id(conn, "SLC")
    add_obs(conn, qid, 13000)  # under the $140 target and at its own low

    rows = report.grid(conn, dest="SLC")
    text = report.render_grid(rows, "SEA → SLC")
    assert "30d low" in text and "under target" in text
    assert "DEST" not in text  # single destination needs no DEST column


def test_render_grid_adds_dest_column_for_shared_labels(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    add_obs(conn, first_query_id(conn, "HND"), 80000)
    add_obs(conn, first_query_id(conn, "NRT"), 70000)

    text = report.render_grid(report.grid(conn, label="Tokyo"), "SEA → Tokyo")
    assert "DEST" in text and "HND" in text and "NRT" in text


def test_empty_grid_renders_without_crashing(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    assert "No observations yet" in report.render_grid([], "SEA → nowhere")
    assert "No observations yet" in report.render_cheapest([])


def test_html_page_is_written(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    add_obs(conn, first_query_id(conn, "SLC"), 15000)

    out = html.write(conn, tmp_path / "sub" / "index.html")
    text = out.read_text()
    assert out.exists()
    assert "<!DOCTYPE html>" in text and "Salt Lake City" in text
    assert "prefers-color-scheme" in text  # readable on a phone at night
    assert "viewport" in text


def test_html_escapes_hostile_text(tmp_path):
    """Airline and label strings come from upstream; never trust them in HTML."""
    conn, cfg = make_db(tmp_path, make_config())
    conn.execute("UPDATE routes SET label='<script>alert(1)</script>' WHERE destination='SLC'")
    conn.commit()
    add_obs(conn, first_query_id(conn, "SLC"), 15000, airline="<img onerror=x>")

    text = html.render(conn)
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text
    assert "<img onerror=x>" not in text


def test_html_renders_with_no_data(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    assert "No observations yet" in html.render(conn)
