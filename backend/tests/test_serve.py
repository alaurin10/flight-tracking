"""serve: the scheduler's clock and the tiny web server."""

import json
import threading
import urllib.request
from datetime import UTC, datetime, timedelta

from helpers import make_config, make_db

from flighttrack import serve
from flighttrack.store.db import open_db


def test_compute_next_is_tomorrow_when_time_has_passed():
    s = serve.Scheduler(at="03:15", jitter_minutes=0, job=lambda: None, log=lambda m: None)
    now = datetime(2027, 1, 1, 12, 0).astimezone()
    nxt = s.compute_next(now)
    assert nxt.date() == (now + timedelta(days=1)).date() and (nxt.hour, nxt.minute) == (3, 15)
    early = datetime(2027, 1, 1, 1, 0).astimezone()
    assert s.compute_next(early).date() == early.date()


def test_jitter_stays_inside_the_window():
    s = serve.Scheduler(at="03:15", jitter_minutes=45, job=lambda: None, log=lambda m: None)
    now = datetime(2027, 1, 1, 12, 0).astimezone()
    for _ in range(20):
        nxt = s.compute_next(now)
        base = (now + timedelta(days=1)).replace(hour=3, minute=15, second=0, microsecond=0)
        assert base <= nxt <= base + timedelta(minutes=45)


def test_trigger_runs_job_once_and_refuses_while_running():
    ran = []
    gate = threading.Event()

    def job():
        ran.append(1)
        gate.wait(timeout=2)

    s = serve.Scheduler(at="03:15", jitter_minutes=0, job=job, log=lambda m: None)
    t = threading.Thread(target=s.loop, daemon=True)
    t.start()
    assert s.trigger()
    for _ in range(50):
        if s.running:
            break
        threading.Event().wait(0.02)
    assert s.running and not s.trigger()  # second trigger refused mid-run
    gate.set()
    for _ in range(50):
        if not s.running:
            break
        threading.Event().wait(0.02)
    assert ran == [1] and s.last_rc == 0
    s.stop()


def test_http_serves_report_status_and_run(tmp_path):
    cfg = make_config(db_path=str(tmp_path / "f.db"), html_path=str(tmp_path / "index.html"))
    conn, cfg = make_db(tmp_path, cfg)
    conn.close()
    jobs = []
    ready = threading.Event()
    port_box = {}

    class _Srv(threading.Thread):
        def run(self):
            # Bind port 0 by constructing the server ourselves.
            from http.server import ThreadingHTTPServer

            sched = serve.Scheduler(
                at="03:15", jitter_minutes=0, job=lambda: jobs.append(1), log=lambda m: None
            )
            threading.Thread(target=sched.loop, daemon=True).start()
            httpd = ThreadingHTTPServer(
                ("127.0.0.1", 0), serve.make_handler(cfg, sched, log=lambda m: None)
            )
            port_box["port"] = httpd.server_address[1]
            port_box["httpd"] = httpd
            port_box["sched"] = sched
            ready.set()
            httpd.serve_forever()

    _Srv(daemon=True).start()
    assert ready.wait(5)
    base = f"http://127.0.0.1:{port_box['port']}"

    with urllib.request.urlopen(base + "/") as r:
        body = r.read().decode()
        assert (
            r.status == 200
            and "<title>flighttrack</title>" in body
            and "No observations yet" in body
        )
    with urllib.request.urlopen(base + "/status.json") as r:
        st = json.loads(r.read())
        assert (
            st["healthy"] is True and st["scheduler"]["running"] is False and st["cheapest"] == []
        )
    req = urllib.request.Request(base + "/run", method="POST")
    with urllib.request.urlopen(req) as r:
        assert r.status == 202
    for _ in range(50):
        if jobs:
            break
        threading.Event().wait(0.02)
    assert jobs == [1]
    try:
        urllib.request.urlopen(base + "/nope")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    port_box["sched"].stop()
    port_box["httpd"].shutdown()


# ---------------------------------------------------------------------------
# M1: start-up catch-up
# ---------------------------------------------------------------------------


def test_needs_catchup_from_last_success(tmp_path):

    from flighttrack.store.db import set_state

    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    conn, _ = make_db(tmp_path)
    assert serve.needs_catchup(conn, now) == (True, "no successful fetch recorded yet")
    set_state(conn, "last_success_at", "2026-11-13T18:00:00Z")  # 18 h ago
    assert serve.needs_catchup(conn, now)[0] is False
    set_state(conn, "last_success_at", "2026-11-13T15:00:00Z")  # 21 h ago
    run, why = serve.needs_catchup(conn, now)
    assert run and "21h ago" in why


def test_cmd_serve_runs_at_start_only_when_behind(tmp_path, monkeypatch, capsys):
    from flighttrack import cli
    from flighttrack.store.db import set_state, utcnow

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seen = []
    monkeypatch.setattr(serve, "serve", lambda cfg, job, **kw: seen.append(kw["run_on_start"]))

    assert cli.main(["serve"]) == 0  # empty database → catch up
    assert "catching up at start" in capsys.readouterr().err  # a JSON log line
    conn = open_db(tmp_path / "flights.db")
    set_state(conn, "last_success_at", utcnow())
    conn.close()
    assert cli.main(["serve"]) == 0  # fresh → wait for the schedule
    assert cli.main(["serve", "--run-on-start"]) == 0
    assert seen == [True, False, True]


def test_serve_no_schedule_serves_only(tmp_path, monkeypatch):
    """With the worker collecting, `serve --no-schedule` runs no job of its own."""
    from flighttrack import cli

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seen = []
    monkeypatch.setattr(serve, "serve", lambda cfg, job, **kw: seen.append(job))
    assert cli.main(["serve", "--no-schedule"]) == 0
    assert seen == [None]


def test_page_only_server_redraws_a_stale_page(tmp_path):
    import os
    import time

    cfg = make_config(db_path=str(tmp_path / "t.db"), html_path=str(tmp_path / "out.html"))
    make_db(tmp_path, cfg)
    page = tmp_path / "out.html"
    page.write_text("<html>STALE-MARKER</html>")
    old = time.time() - 120
    os.utime(page, (old, old))
    handler = serve.make_handler(cfg, None, log=lambda m: None)
    httpd = serve.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        body = urllib.request.urlopen(url, timeout=5).read().decode()
    finally:
        t.join(5)
        httpd.server_close()
    assert "STALE-MARKER" not in body and "<html" in body.lower()
