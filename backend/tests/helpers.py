"""Shared fixtures. Everything here is offline — no test touches the network."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from flighttrack import watches
from flighttrack.config import (
    Alerts,
    Config,
    DealWatch,
    Fetch,
    Pattern,
    PatternEntry,
    Route,
    Search,
    Trip,
    WatchFile,
)
from flighttrack.store.db import open_db

DEFAULT_ROUTES = [
    Route("HND", "Tokyo", 65000, 1, None),
    Route("NRT", "Tokyo", 60000, 2, None),
    Route("SLC", "Salt Lake City", 14000, 1, None),
]
DEFAULT_PATTERNS = [
    Pattern("extended_weekend_thu", "THU", 4),
    Pattern("full_week", "SAT", 7),
]


@dataclass(frozen=True)
class WatchedConfig(Config):
    """Tunables plus the watches a test starts with, in the old config-file shape.

    Only the tests carry these: `make_db` loads them into the database through
    `watches.import_watch_file`, the same path as `flighttrack import-config`, and
    everything after that reads the database.
    """

    routes: list[Route] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    trips: list[Trip] = field(default_factory=list)
    deal_watches: list[DealWatch] = field(default_factory=list)

    def route(self, dest: str) -> Route | None:
        return next((r for r in self.routes if r.dest == dest), None)


def make_config(**over) -> WatchedConfig:
    base = dict(
        home="SEA",
        routes=list(DEFAULT_ROUTES),
        patterns=list(DEFAULT_PATTERNS),
        min_days_ahead=21,
        max_days_ahead=60,
        search=Search(),
        fetch=Fetch(max_queries_per_run=5, min_sleep_seconds=0, max_sleep_seconds=0),
        alerts=Alerts(),
        db_path=":memory:",
    )
    base.update(over)
    return WatchedConfig(**base)


def watch_file(cfg) -> WatchFile:
    return WatchFile(
        home=cfg.home,
        routes=list(getattr(cfg, "routes", [])),
        patterns=[PatternEntry(p) for p in getattr(cfg, "patterns", [])],
        trips=list(getattr(cfg, "trips", [])),
        deal_watches=list(getattr(cfg, "deal_watches", [])),
        deal_feeds=[],
    )


def load_watches(conn, cfg, today=None, build_links=False):
    """Load (or update) the test config's watches through the import path."""
    return watches.import_watch_file(
        conn, cfg, watch_file(cfg), today=today, build_links=build_links
    )


def make_db(tmp_path, cfg=None, build_links=False, today=None):
    """`today` pins the date the grid is expanded from, for tests that name specific dates."""
    cfg = cfg or make_config()
    conn = open_db(tmp_path / "t.db")
    load_watches(conn, cfg, today=today, build_links=build_links)
    return conn, cfg


def add_obs(conn, query_id, price_cents, days_ago=0, is_best=1, airline="Alaska", stops=0):
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        """INSERT INTO observations
           (query_id, observed_at, price_cents, currency, airline, stops, duration_min,
            is_best, raw)
           VALUES (?, ?, ?, 'USD', ?, ?, 600, ?, '{}')""",
        (query_id, ts, price_cents, airline, stops, is_best),
    )
    conn.commit()
    return cur.lastrowid


def first_query_id(conn, dest="SLC"):
    return conn.execute(
        "SELECT q.id FROM queries q JOIN routes r ON r.id=q.route_id "
        "WHERE r.destination=? ORDER BY q.depart_date LIMIT 1",
        (dest,),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Google Flights page fixtures (shape mirrors the live ds:1 payload)
# ---------------------------------------------------------------------------


def make_segment(
    origin="SEA",
    dest="HND",
    dep=(2027, 1, 7),
    dep_t=(8, 30),
    arr=(2027, 1, 8),
    arr_t=(11, 5),
    minutes=615,
    plane="Boeing 787",
    carrier="AS",
    number="171",
):
    s = [None] * 23
    s[3], s[4], s[5], s[6] = origin, f"{origin} name", f"{dest} name", dest
    s[8], s[10], s[11], s[17] = list(dep_t), list(arr_t), minutes, plane
    s[20], s[21], s[22] = list(dep), list(arr), [carrier, number]
    return s


def make_itinerary(price, segments=None, codes=("AS",)):
    flight = [None] * 23
    flight[0] = "itinerary"
    flight[1] = list(codes)
    flight[2] = segments if segments is not None else [make_segment()]
    flight[22] = [None] * 9
    return [flight, [[None, price]]]


def make_payload(
    best=(), other=(), airlines=(("AS", "Alaska"), ("DL", "Delta"), ("JL", "Japan Airlines"))
):
    payload = [None] * 8
    payload[2] = [list(best)] if best is not None else None  # "Top departing flights"
    payload[3] = [list(other)] if other is not None else None  # "Other departing flights"
    payload[7] = [None, [[["STAR", "Star Alliance"]], [list(a) for a in airlines]]]
    return payload


def make_results_html(payload, wrap="script"):
    import json as _json

    js = (
        "AF_initDataCallback({key: 'ds:1', hash: '2', data:"
        + _json.dumps(payload)
        + ", sideChannel: {}});"
    )
    if wrap == "script":
        return (
            "<html><head><title>Flights</title></head><body>"
            f'<script class="ds:1" nonce="abc">{js}</script></body></html>'
        )
    return f"<html><body><script>{js}</script></body></html>"


NO_RESULTS_HTML = (
    "<html><body><script class=\"ds:1\" nonce=\"abc\">AF_initDataCallback({key: 'ds:1', hash: '2', "
    "data:{}, sideChannel: {}, errorHasStatus: true});</script></body></html>"
)


class FakeTransport:
    """Scripted responses: each call pops the next (status, url, text) or raises."""

    name = "fake"

    def __init__(self, responses):
        from flighttrack.gflights.transport import TransportError

        self._err = TransportError
        self.responses = list(responses)
        self.calls: list[dict] = []

    def _next(self, url, params, data=None):
        from flighttrack.gflights.transport import Response

        self.calls.append({"url": url, "params": params, "data": data})
        if not self.responses:
            raise self._err("no scripted response left")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, final_url, text = item
        return Response(
            status=status,
            url=final_url or url,
            text=text,
            elapsed=0.01,
            transport="fake",
            headers={},
        )

    def get(self, url, params=None, headers=None, timeout=30.0):
        return self._next(url, params)

    def post(self, url, data, params=None, headers=None, timeout=30.0):
        return self._next(url, params, data)


def load_old(path, strict_secrets=True, settings=None) -> WatchedConfig:
    """An old single-file config: the tracked part validated by `parse_watch_file` (what
    `import-config` uses), the rest by `config.load` as the tunables file."""
    import dataclasses
    from pathlib import Path

    import yaml

    from flighttrack.config import WATCH_SECTIONS, ConfigError, load, parse_watch_file
    from flighttrack.settings import Settings

    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    wf = parse_watch_file(raw)
    tunables = {k: v for k, v in raw.items() if k not in WATCH_SECTIONS}
    if isinstance(tunables.get("deals"), dict):
        tunables["deals"] = {k: v for k, v in tunables["deals"].items() if k != "watches"}
    import tempfile

    tfile = Path(tempfile.mkdtemp(prefix="flighttrack-test-")) / "flighttrack.yaml"
    tfile.write_text(yaml.safe_dump(tunables))
    settings = settings or Settings(home_airport=wf.home)
    cfg = load(tfile, strict_secrets=strict_secrets, settings=settings)
    return WatchedConfig(
        **{f.name: getattr(cfg, f.name) for f in dataclasses.fields(Config)},
        routes=wf.routes,
        patterns=[e.pattern for e in wf.patterns],
        trips=wf.trips,
        deal_watches=wf.deal_watches,
    )


# ---------------------------------------------------------------------------
# A clock the tests move by hand (store.db.set_clock): the worker's decisions and
# every timestamp it writes follow it, so days of running take no real time.
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw: float) -> datetime:
        self.t += timedelta(**kw)
        return self.t

    def set(self, t: datetime) -> None:
        self.t = t
