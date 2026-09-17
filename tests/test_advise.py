"""Book-or-wait verdicts are explainable arithmetic over the history."""

from datetime import date, timedelta

from flighttrack import report
from helpers import add_obs, first_query_id, make_config, make_db


def test_book_when_under_target_at_low_and_cheap_for_route(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    qid = first_query_id(conn, "SLC")
    for i, p in enumerate([20000, 19500, 19000, 18500, 18000, 17500]):
        add_obs(conn, qid, p, days_ago=10 - i)
    add_obs(conn, qid, 13000)                        # under $140 target, all-time low, p20
    items = report.advise(conn, dest="SLC")
    top = items[0]
    assert top.verdict == "BOOK" and top.row.current_cents == 13000
    assert any("target" in r for r in top.reasons) and any("lowest" in r for r in top.reasons)
    text = report.render_advice(items, "t")
    assert "BOOK" in text and "Heuristic" in text


def test_wait_when_above_median_and_far_out(tmp_path):
    cfg = make_config(max_days_ahead=200)
    conn, cfg = make_db(tmp_path, cfg)
    far = conn.execute(
        "SELECT q.id FROM queries q JOIN routes r ON r.id = q.route_id WHERE r.destination = 'HND' AND q.depart_date > ? ORDER BY q.depart_date DESC LIMIT 1",
        ((date.today() + timedelta(days=150)).isoformat(),),
    ).fetchone()[0]
    near = first_query_id(conn, "HND")
    for p in [80000, 82000, 84000]:
        add_obs(conn, near, p, days_ago=2)
    add_obs(conn, far, 120000)                       # above the route median, 150+ days out
    items = report.advise(conn, dest="HND")
    verdicts = {a.row.query_id: a.verdict for a in items}
    assert verdicts[far] == "WAIT"


def test_grid_sort_by_price_and_sweep_column(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    a, b = first_query_id(conn, "SLC"), first_query_id(conn, "HND")
    add_obs(conn, a, 30000)
    add_obs(conn, b, 20000)
    conn.execute("INSERT INTO observations (query_id, observed_at, price_cents, is_best, source) VALUES (?, '2026-09-17T00:00:00Z', 18000, 0, 'google_calendar')", (b,))
    conn.commit()
    rows = report.grid(conn, sort="price")
    assert [r.query_id for r in rows][:2] == [b, a]
    assert rows[0].sweep_cents == 18000 and rows[0].current_cents == 20000   # sweep never becomes 'current'
    assert "SWEEP" in report.render_grid(rows, "t")
