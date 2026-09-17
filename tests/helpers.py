"""Shared fixtures. Everything here is offline — no test touches the network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flighttrack import db, expand
from flighttrack.config import Alerts, Config, Fetch, Pattern, Route, Search


def make_config(**over) -> Config:
    base = dict(
        home="SEA",
        routes=[
            Route("HND", "Tokyo", 65000, 1, None),
            Route("NRT", "Tokyo", 60000, 2, None),
            Route("SLC", "Salt Lake City", 14000, 1, None),
        ],
        patterns=[
            Pattern("extended_weekend_thu", "THU", 4),
            Pattern("full_week", "SAT", 7),
        ],
        min_days_ahead=21,
        max_days_ahead=60,
        search=Search(),
        fetch=Fetch(max_queries_per_run=5, min_sleep_seconds=0, max_sleep_seconds=0),
        alerts=Alerts(),
        notify={"channel": "none"},
        db_path=":memory:",
    )
    base.update(over)
    return Config(**base)


def make_db(tmp_path, cfg=None, build_links=False):
    cfg = cfg or make_config()
    conn = db.connect(tmp_path / "t.db")
    expand.expand(conn, cfg, build_links=build_links)
    return conn, cfg


def add_obs(conn, query_id, price_cents, days_ago=0, is_best=1, airline="Alaska", stops=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        """INSERT INTO observations
           (query_id, observed_at, price_cents, currency, airline, stops, duration_min, is_best, raw)
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

def make_segment(origin="SEA", dest="HND", dep=(2027, 1, 7), dep_t=(8, 30), arr=(2027, 1, 8),
                 arr_t=(11, 5), minutes=615, plane="Boeing 787", carrier="AS", number="171"):
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


def make_payload(best=(), other=(), airlines=(("AS", "Alaska"), ("DL", "Delta"), ("JL", "Japan Airlines"))):
    payload = [None] * 8
    payload[2] = [list(other)] if other is not None else None
    payload[3] = [list(best)] if best is not None else None
    payload[7] = [None, [[["STAR", "Star Alliance"]], [list(a) for a in airlines]]]
    return payload


def make_results_html(payload, wrap="script"):
    import json as _json

    js = ("AF_initDataCallback({key: 'ds:1', hash: '2', data:" + _json.dumps(payload) + ", sideChannel: {}});")
    if wrap == "script":
        return f"<html><head><title>Flights</title></head><body><script class=\"ds:1\" nonce=\"abc\">{js}</script></body></html>"
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
        return Response(status=status, url=final_url or url, text=text, elapsed=0.01, transport="fake", headers={})

    def get(self, url, params=None, headers=None, timeout=30.0):
        return self._next(url, params)

    def post(self, url, data, params=None, headers=None, timeout=30.0):
        return self._next(url, params, data)
