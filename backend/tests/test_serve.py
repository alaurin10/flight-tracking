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
    from datetime import timezone

    from flighttrack.db import set_state

    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    conn, _ = make_db(tmp_path)
    assert serve.needs_catchup(conn, now) == (True, "no successful fetch recorded yet")
    set_state(conn, "last_success_at", "2026-11-13T18:00:00Z")  # 18 h ago
    assert serve.needs_catchup(conn, now)[0] is False
    set_state(conn, "last_success_at", "2026-11-13T15:00:00Z")  # 21 h ago
    run, why = serve.needs_catchup(conn, now)
    assert run and "21h ago" in why


def test_cmd_serve_runs_at_start_only_when_behind(tmp_path, monkeypatch, capsys):
    from flighttrack import cli
    from flighttrack.db import set_state, utcnow

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "home: SEA\nroutes:\n  - dest: SLC\n    label: Salt Lake City\npatterns:\n  - name: fri\n    depart_dow: FRI\n"
        f"    nights: 3\nnotify:\n  channel: none\noutput:\n  db_path: {tmp_path / 'f.db'}\n  html_path: {tmp_path / 'i.html'}\n"
    )
    seen = []
    monkeypatch.setattr(serve, "serve", lambda cfg, job, **kw: seen.append(kw["run_on_start"]))

    assert cli.main(["--config", str(cfg_path), "serve"]) == 0  # empty database → catch up
    assert "catching up at start" in capsys.readouterr().out
    conn = connect(tmp_path / "f.db")
    set_state(conn, "last_success_at", utcnow())
    conn.close()
    assert cli.main(["--config", str(cfg_path), "serve"]) == 0  # fresh → wait for the schedule
    assert cli.main(["--config", str(cfg_path), "serve", "--run-on-start"]) == 0
    assert seen == [True, False, True]
