"""One long-running process: a scheduler for the daily job and a small web server.

This is the "how do I actually run it" answer for a mini PC or any always-on
box. `flighttrack serve` replaces cron *and* a web server:

  * at `--at HH:MM` local time each day (plus up to `--jitter` minutes so the
    request pattern is not a fixed signature) it runs the daily job in-process;
  * it serves the report at `/`, a machine-readable status at `/status.json`,
    and accepts `POST /run` to trigger a run now (for a phone bookmark or a
    home-automation button).

Stdlib only: `http.server` + `threading`. It is not a public web server; put it
behind Tailscale or your LAN, never on the open internet.
"""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import fetch as fetch_mod
from . import health as health_mod
from . import html as html_mod
from .config import Config
from .db import connect, get_state, utcnow


@dataclass
class Scheduler:
    """Runs `job` once a day at a local wall-clock time, with jitter."""

    at: str                                   # "HH:MM" local time
    jitter_minutes: int
    job: Callable[[], None]
    log: Callable[[str], None] = print
    next_run: datetime | None = None
    last_started: datetime | None = None
    last_finished: datetime | None = None
    last_rc: int | None = None
    running: bool = False
    _stop: threading.Event = field(default_factory=threading.Event)
    _kick: threading.Event = field(default_factory=threading.Event)

    def compute_next(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now().astimezone()
        hh, mm = (int(x) for x in self.at.split(":"))
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate + timedelta(minutes=random.uniform(0, max(0, self.jitter_minutes)))

    def trigger(self) -> bool:
        """Ask for a run now. Returns False if one is already running."""
        if self.running:
            return False
        self._kick.set()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()

    def _run_once(self, reason: str) -> None:
        self.running = True
        self.last_started = datetime.now().astimezone()
        self.log(f"[serve] run starting ({reason}) at {self.last_started:%Y-%m-%d %H:%M %Z}")
        try:
            self.job()
            self.last_rc = 0
        except Exception as exc:  # noqa: BLE001 — the loop must survive anything
            self.last_rc = 1
            self.log(f"[serve] run crashed: {type(exc).__name__}: {exc}")
        finally:
            self.last_finished = datetime.now().astimezone()
            self.running = False

    def loop(self, run_on_start: bool = False) -> None:
        if run_on_start:
            self._run_once("startup")
        self.next_run = self.compute_next()
        self.log(f"[serve] next scheduled run {self.next_run:%Y-%m-%d %H:%M %Z}")
        while not self._stop.is_set():
            wait = max(1.0, (self.next_run - datetime.now().astimezone()).total_seconds())
            kicked = self._kick.wait(timeout=min(wait, 60.0))
            if self._stop.is_set():
                break
            if kicked:
                self._kick.clear()
                self._run_once("manual trigger")
                continue
            if datetime.now().astimezone() >= self.next_run:
                self._run_once("schedule")
                self.next_run = self.compute_next()
                self.log(f"[serve] next scheduled run {self.next_run:%Y-%m-%d %H:%M %Z}")


def status_payload(cfg: Config, sched: Scheduler | None) -> dict:
    """Everything a phone widget or a home dashboard would want, as JSON."""
    conn = connect(cfg.db_path)
    try:
        rep = health_mod.check(conn, cfg)
        last_run = conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
        cheapest = conn.execute(
            """
            WITH latest AS (
                SELECT query_id, price_cents, ROW_NUMBER() OVER (PARTITION BY query_id ORDER BY observed_at DESC, id DESC) rn
                FROM observations WHERE is_best = 1
            )
            SELECT r.label, r.destination, q.depart_date, q.return_date, l.price_cents, q.deep_link
            FROM latest l JOIN queries q ON q.id = l.query_id JOIN routes r ON r.id = q.route_id
            WHERE l.rn = 1 AND q.active = 1 AND r.active = 1
            ORDER BY l.price_cents ASC LIMIT 5
            """
        ).fetchall()
        return {
            "generated_at": utcnow(),
            "healthy": rep.ok,
            "problems": [{"code": p.code, "severity": p.severity, "message": p.message} for p in rep.problems],
            "cooldown_until": fetch_mod.cooldown_active(conn),
            "last_success_at": get_state(conn, fetch_mod.STATE_LAST_SUCCESS),
            "last_run": dict(last_run) if last_run else None,
            "scheduler": None if sched is None else {
                "running": sched.running,
                "next_run": sched.next_run.isoformat() if sched.next_run else None,
                "last_started": sched.last_started.isoformat() if sched.last_started else None,
                "last_finished": sched.last_finished.isoformat() if sched.last_finished else None,
                "last_rc": sched.last_rc,
            },
            "cheapest": [
                {"label": c["label"], "dest": c["destination"], "depart": c["depart_date"], "return": c["return_date"],
                 "price": c["price_cents"] / 100, "link": c["deep_link"]}
                for c in cheapest
            ],
        }
    finally:
        conn.close()


def make_handler(cfg: Config, sched: Scheduler | None, log: Callable[[str], None] = print):
    html_path = Path(cfg.html_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "flighttrack/0.2"

        def log_message(self, fmt, *args):  # quieter than the default
            log(f"[http] {self.address_string()} {fmt % args}")

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                if not html_path.exists():
                    conn = connect(cfg.db_path)
                    try:
                        html_mod.write(conn, html_path, cfg=cfg)
                    finally:
                        conn.close()
                self._send(HTTPStatus.OK, html_path.read_bytes(), "text/html; charset=utf-8")
            elif path in ("/status.json", "/health.json"):
                body = json.dumps(status_payload(cfg, sched), default=str, indent=2).encode()
                self._send(HTTPStatus.OK, body, "application/json")
            elif path == "/run":
                self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"POST /run to trigger a run\n", "text/plain")
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/run":
                if sched is None:
                    self._send(HTTPStatus.SERVICE_UNAVAILABLE, b"no scheduler\n", "text/plain")
                elif sched.trigger():
                    self._send(HTTPStatus.ACCEPTED, b"run triggered\n", "text/plain")
                else:
                    self._send(HTTPStatus.CONFLICT, b"a run is already in progress\n", "text/plain")
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")

    return Handler


def serve(cfg: Config, job: Callable[[], None], host: str = "0.0.0.0", port: int = 8080,
          at: str = "03:15", jitter_minutes: int = 45, run_on_start: bool = False,
          log: Callable[[str], None] = print, ready: threading.Event | None = None) -> None:
    sched = Scheduler(at=at, jitter_minutes=jitter_minutes, job=job, log=log)
    t = threading.Thread(target=sched.loop, kwargs={"run_on_start": run_on_start}, name="flighttrack-scheduler", daemon=True)
    t.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg, sched, log))
    log(f"[serve] report at http://{host}:{httpd.server_address[1]}/  ·  status at /status.json  ·  POST /run to run now")
    if ready is not None:
        ready.set()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sched.stop()
        httpd.server_close()
