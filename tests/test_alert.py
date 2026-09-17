"""Alerting: the two phases, plus the dedup that keeps it from becoming noise."""

from dataclasses import replace

from flighttrack import alert
from helpers import add_obs, first_query_id, make_config, make_db


def _cfg(**over):
    cfg = make_config(notify={"channel": "none"})
    if over:
        cfg = replace(cfg, alerts=replace(cfg.alerts, **over))
    return cfg


def test_quiet_when_nothing_qualifies(tmp_path):
    cfg = _cfg()
    conn, cfg = make_db(tmp_path, cfg)
    add_obs(conn, first_query_id(conn, "SLC"), 50000)  # far above the $140 target
    stats = alert.run(conn, cfg, verbose=False)
    assert stats.qualified == 0 and stats.sent == 0


def test_threshold_fires_at_or_below_target(tmp_path):
    cfg = _cfg()
    conn, cfg = make_db(tmp_path, cfg)
    add_obs(conn, first_query_id(conn, "SLC"), 14000)  # exactly the target
    stats = alert.run(conn, cfg, verbose=False)
    assert stats.qualified == 1 and stats.sent == 1
    assert conn.execute("SELECT reason FROM alerts_sent").fetchone()[0] == "threshold"


def test_all_time_low_needs_enough_priors(tmp_path):
    """The first sighting must not be trivially a record."""
    cfg = _cfg()
    conn, cfg = make_db(tmp_path, cfg)
    qid = first_query_id(conn, "HND")

    add_obs(conn, qid, 100000, days_ago=3)
    add_obs(conn, qid, 99000, days_ago=2)
    add_obs(conn, qid, 98000, days_ago=1)  # a new low, but only 2 priors
    assert alert.run(conn, cfg, verbose=False).qualified == 0

    for i, p in enumerate([97000, 96000, 95000]):
        add_obs(conn, qid, p, days_ago=0)
    add_obs(conn, qid, 90000, days_ago=0)  # now 6 priors exist
    stats = alert.run(conn, cfg, verbose=False)
    assert stats.qualified == 1
    assert conn.execute("SELECT reason FROM alerts_sent").fetchone()[0] == "all_time_low"


def test_stale_observations_are_ignored(tmp_path):
    """Only fresh data alerts, or a cheap fare would re-fire forever."""
    cfg = _cfg(consider_observations_within_hours=48)
    conn, cfg = make_db(tmp_path, cfg)
    add_obs(conn, first_query_id(conn, "SLC"), 10000, days_ago=5)
    assert alert.run(conn, cfg, verbose=False).considered == 0


def test_dedup_suppresses_a_repeat_within_the_window(tmp_path):
    cfg = _cfg()
    conn, cfg = make_db(tmp_path, cfg)
    qid = first_query_id(conn, "SLC")

    add_obs(conn, qid, 13000)
    assert alert.run(conn, cfg, verbose=False).sent == 1

    add_obs(conn, qid, 12900)  # only 0.8% lower
    stats = alert.run(conn, cfg, verbose=False)
    assert stats.suppressed_dedup == 1 and stats.sent == 0


def test_dedup_lets_a_further_drop_through(tmp_path):
    cfg = _cfg(dedup_further_drop_pct=10)
    conn, cfg = make_db(tmp_path, cfg)
    qid = first_query_id(conn, "SLC")

    add_obs(conn, qid, 13000)
    alert.run(conn, cfg, verbose=False)

    add_obs(conn, qid, 11000)  # 15% further down
    stats = alert.run(conn, cfg, verbose=False)
    assert stats.sent == 1 and stats.suppressed_dedup == 0


def test_digest_replaces_a_flood(tmp_path):
    cfg = _cfg(max_per_run=3)
    conn, cfg = make_db(tmp_path, cfg)
    qids = [r[0] for r in conn.execute(
        "SELECT q.id FROM queries q JOIN routes r ON r.id=q.route_id "
        "WHERE r.destination='SLC' LIMIT 5")]
    for qid in qids:
        add_obs(conn, qid, 12000)

    stats = alert.run(conn, cfg, verbose=False)
    assert stats.qualified == 5
    assert stats.sent == 1 and stats.digested == 5  # one digest, not five pushes
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 5


def test_delivery_failure_is_not_recorded_as_sent(tmp_path):
    """Otherwise the dedup window would suppress the retry."""
    cfg = replace(_cfg(), notify={"channel": "ntfy", "ntfy": {"topic": None}})
    conn, cfg = make_db(tmp_path, cfg)
    add_obs(conn, first_query_id(conn, "SLC"), 13000)

    stats = alert.run(conn, cfg, verbose=False)
    assert stats.delivery_error is not None
    assert stats.sent == 0
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0


def test_phase2_is_off_until_enabled(tmp_path):
    cfg = _cfg(percentile_phase2_enabled=False)
    conn, cfg = make_db(tmp_path, cfg)
    qid = first_query_id(conn, "HND")
    for i in range(40):
        add_obs(conn, qid, 100000 - i * 100, days_ago=40 - i)
    add_obs(conn, qid, 99999, days_ago=0)  # cheap by percentile, not by target
    assert alert.run(conn, cfg, verbose=False).qualified == 0


def test_phase2_needs_enough_history(tmp_path):
    cfg = _cfg(percentile_phase2_enabled=True, history_days_required=30)
    conn, cfg = make_db(tmp_path, cfg)
    qid = first_query_id(conn, "HND")
    for i in range(10):  # only 10 days of history
        add_obs(conn, qid, 100000, days_ago=10 - i)
    add_obs(conn, qid, 80000, days_ago=0)
    stats = alert.run(conn, cfg, verbose=False)
    assert all(r[0] != "percentile" for r in conn.execute("SELECT reason FROM alerts_sent"))


def test_phase2_fires_on_a_route_level_percentile(tmp_path):
    cfg = _cfg(percentile_phase2_enabled=True, history_days_required=30, percentile=20)
    conn, cfg = make_db(tmp_path, cfg)

    # Spread history across several HND date pairs: the baseline is the ROUTE's,
    # not this one date pair's.
    qids = [r[0] for r in conn.execute(
        "SELECT q.id FROM queries q JOIN routes r ON r.id=q.route_id "
        "WHERE r.destination='HND' LIMIT 4")]
    for qid in qids:
        for i in range(40):
            add_obs(conn, qid, 100000 + (i % 7) * 2000, days_ago=40 - i)

    # Give this pair its own cheaper past, so the new price is NOT an all-time
    # low for it — isolating the percentile rule as the only thing that can fire.
    add_obs(conn, qids[0], 90000, days_ago=39)

    add_obs(conn, qids[0], 95000, days_ago=0)  # below route p20, above the $650 target
    alert.run(conn, cfg, verbose=False)
    reasons = {r[0] for r in conn.execute("SELECT reason FROM alerts_sent")}
    assert reasons == {"percentile"}


def test_percentile_helper():
    assert alert.percentile([], 20) is None
    assert alert.percentile([100], 20) == 100
    assert alert.percentile(list(range(1, 101)), 20) == 20
    assert alert.percentile([5, 1, 3, 2, 4], 20) == 1
