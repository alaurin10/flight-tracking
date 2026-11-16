"""`flighttrack demo`: the synthetic database the app and the e2e tests are shown on."""

from datetime import date

from helpers import make_config

from flighttrack import demo
from flighttrack.config import Trip
from flighttrack.store.db import open_db

TODAY = date(2026, 11, 14)


def _demo(tmp_path):
    cfg = make_config(
        trips=[Trip("japan", "HND", "2027-04-10", "2027-04-24", target_price=80000)],
        max_days_ahead=90,
    )
    conn = open_db(tmp_path / "demo.db")
    demo.seed(conn, cfg, days=20, today=TODAY)  # pinned: the 2027 trip must still be ahead
    return conn, cfg


def test_demo_seed_populates_everything(tmp_path):
    conn, _cfg = _demo(tmp_path)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM observations WHERE source = 'demo' AND is_best = 1"
        ).fetchone()[0]
        > 100
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM observations WHERE source = 'google_calendar'"
        ).fetchone()[0]
        > 0
    )
    assert conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0] >= 14
    assert (
        conn.execute("SELECT COUNT(*) FROM deal_posts WHERE watch IS NOT NULL").fetchone()[0] == 3
    )


def test_demo_seed_records_alerts_like_the_worker(tmp_path):
    conn, _cfg = _demo(tmp_path)
    kinds = [r[0] for r in conn.execute("SELECT kind FROM notifications ORDER BY id")]
    assert "deal" in kinds
    trips = conn.execute("SELECT COUNT(*) FROM trips WHERE status = 'active'").fetchone()[0]
    assert kinds.count("price") == trips == 1
    price = conn.execute("SELECT title, url FROM notifications WHERE kind = 'price'").fetchone()
    assert price["url"] == "/trip/tokyo-spring" and price["title"].startswith("$")
    sent = conn.execute("SELECT * FROM alerts_sent").fetchone()
    low = conn.execute(
        "SELECT MIN(price_cents) FROM observations WHERE query_id = ? AND is_best = 1",
        (sent["query_id"],),
    ).fetchone()[0]
    assert sent["price_cents"] == low and sent["reason"] == "all_time_low"
