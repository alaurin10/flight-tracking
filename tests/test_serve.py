"""serve: the scheduler's clock and the tiny web server."""

import json
import threading
import urllib.request
from datetime import datetime, timedelta

from flighttrack import serve
from flighttrack.db import connect
from helpers import make_config, make_db


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
    assert s.running and not s.trigger()      # second trigger refused mid-run
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

            sched = serve.Scheduler(at="03:15", jitter_minutes=0, job=lambda: jobs.append(1), log=lambda m: None)
            threading.Thread(target=sched.loop, daemon=True).start()
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(cfg, sched, log=lambda m: None))
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
        assert r.status == 200 and "<title>flighttrack</title>" in body and "No observations yet" in body
    with urllib.request.urlopen(base + "/status.json") as r:
        st = json.loads(r.read())
        assert st["healthy"] is True and st["scheduler"]["running"] is False and st["cheapest"] == []
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
