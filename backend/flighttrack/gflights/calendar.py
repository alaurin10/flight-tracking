"""EXPERIMENTAL — the price-calendar RPC behind Google Flights' "price graph".

Why it matters: one request answers "what does every departure date in this
window cost for an N-night trip", which is precisely the ski-weekend question
and is 30–60× cheaper than fetching each weekend's results page. It turns a
week-long rotation into a daily sweep.

Why it is experimental: it is an internal batchexecute endpoint. The request
body layout below was reconstructed from open-source clients and browser
traffic. It was checked against live Google from a cloud sandbox on
2026-11-14 (HTTP 200, 31 dated prices for a 31-day window, the first equal to
the results page with the same bag filter), but it can change without notice.
Run

    flighttrack doctor --calendar

on the host before enabling `calendar.enabled`. The parser is deliberately
shape-agnostic: it walks the decoded response for `[date, …, price]` tuples
instead of trusting fixed indices, so modest changes in the envelope do not
break it, and `doctor --calendar --dump` saves the raw response for a human
to look at when it does.

Calendar prices include basic economy (the exclude-basic slot is not known),
so they are compared only with other calendar prices, never with the tracked
results-page series.

Numbers from this endpoint are Google's cached "lowest price" per date; they
are indicative, not a quote. The fetcher records them with source
'google_calendar' and the alerting layer confirms a candidate with a real
results fetch before notifying.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .tfs import SEATS

RPC_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetCalendarGraph"
)
RPC_PARAMS = {
    "hl": "en",
    "gl": "US",
    "curr": "USD",
    "rt": "c",
    "soc-app": "162",
    "soc-platform": "1",
    "soc-device": "1",
}
RPC_HEADERS = {
    "X-Same-Domain": "1",
    "Origin": "https://www.google.com",
    "Referer": "https://www.google.com/travel/flights",
}

# Filter-array slot for "exclude basic economy", once someone finds it (see
# docs/PLAN.md, "Calendar filter slots"). None = unknown, so calendar prices
# include basic economy fares.
EXCLUDE_BASIC_SLOT: int | None = None

# Largest window observed to come back fully populated. Larger requests are
# chunked by `windows()`.
MAX_WINDOW_DAYS = 61

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class DatePrice:
    depart: str
    ret: str | None
    price: int | float


def _segment(origin: str, dest: str, day: str, max_stops: int | None) -> list:
    # stops enum on this endpoint: 0 any, 1 nonstop, 2 ≤1 stop, 3 ≤2 stops
    stops = 0 if max_stops is None else min(3, max_stops + 1)
    return [
        [[[origin, 0]]],
        [[[dest, 0]]],
        None,
        stops,
        [],
        [],
        day,
        None,
        [],
        [],
        [],
        None,
        None,
        [],
        3,
    ]


def build_body(
    origin: str,
    dest: str,
    start: str,
    end: str,
    nights: int | None,
    *,
    seat: str = "economy",
    adults: int = 1,
    max_stops: int | None = None,
    currency: str = "USD",
    carry_on_bags: int = 0,
    checked_bags: int = 0,
) -> str:
    """The `f.req=` form body. `nights=None` means one-way.

    Filter slot 10 is `[carry_on, checked]` bags (verified live 2026-11-14:
    `[1, 0]` moved SEA→SLC Jan 15 from $304 to $347, the carry-on results
    price). The slot for "exclude basic economy" is not known yet; see
    docs/PLAN.md, "Calendar filter slots".
    """
    round_trip = nights is not None
    trip_type = 1 if round_trip else 2
    anchor_out = start
    anchor_back = (
        (date.fromisoformat(start) + timedelta(days=nights)).isoformat() if round_trip else None
    )
    segments = [_segment(origin, dest, anchor_out, max_stops)]
    if round_trip:
        segments.append(_segment(dest, origin, anchor_back, max_stops))
    bags = [carry_on_bags, checked_bags] if (carry_on_bags or checked_bags) else None
    inner = [
        None,
        [
            None,
            None,
            trip_type,
            None,
            [],
            SEATS[seat],
            [adults, 0, 0, 0],
            None,
            None,
            None,
            bags,
            None,
            None,
            segments,
            None,
            None,
            None,
            1,
            None,
            None,
            None,
            None,
            None,
            [],
        ],
        [start, end],
        None,
        [nights, nights] if round_trip else None,
    ]
    payload = json.dumps([None, json.dumps(inner, separators=(",", ":"))], separators=(",", ":"))
    return "f.req=" + urllib.parse.quote(payload, safe="") + "&"


def windows(start: date, end: date, size: int = MAX_WINDOW_DAYS) -> list[tuple[str, str]]:
    """Split [start, end] into inclusive chunks the endpoint will fill."""
    out: list[tuple[str, str]] = []
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=size - 1))
        out.append((cur.isoformat(), stop.isoformat()))
        cur = stop + timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def unwrap_batchexecute(text: str) -> list[Any]:
    """Return every decoded `wrb.fr` inner payload in a batchexecute response.

    The envelope is `)]}'` then length-prefixed JSON chunks. We ignore the
    length prefixes and simply try to decode every line that looks like a
    JSON array; the ones we want are `["wrb.fr", …, "<json string>", …]`.
    """
    text = text.lstrip()
    if text.startswith(")]}'"):
        text = text[4:]
    found: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        for entry in chunk if isinstance(chunk, list) else []:
            if isinstance(entry, list) and entry and entry[0] == "wrb.fr":
                for cell in entry[1:]:
                    if isinstance(cell, str) and cell.startswith("["):
                        try:
                            found.append(json.loads(cell))
                            break
                        except json.JSONDecodeError:
                            continue
    return found


def _first_price(node: Any) -> int | float | None:
    """Depth-first: the first positive number that is not a date component."""
    if isinstance(node, bool):
        return None
    if isinstance(node, (int, float)):
        return node if node > 0 else None
    if isinstance(node, (list, tuple)):
        for child in node:
            p = _first_price(child)
            if p is not None:
                return p
    return None


def _walk(node: Any, out: list[DatePrice], seen: set[tuple]) -> None:
    if not isinstance(node, (list, tuple)):
        return
    if node and isinstance(node[0], str) and _ISO.match(node[0]):
        ret = (
            node[1] if len(node) > 1 and isinstance(node[1], str) and _ISO.match(node[1]) else None
        )
        # The price sits in a nested structure after the date(s). Skip the
        # date strings themselves so a year is never mistaken for a fare.
        tail = node[2:] if ret else node[1:]
        price = _first_price(tail)
        if price is not None:
            key = (node[0], ret)
            if key not in seen:
                seen.add(key)
                out.append(DatePrice(node[0], ret, price))
            return
    for child in node:
        _walk(child, out, seen)


# A batchexecute entry whose payload is null: `["wrb.fr","<rpc>",null,…` or, as captured
# on 2026-11-16 (tests/fixtures/live-20261116), `["wrb.fr",null,null,…`.
_EMPTY_ENTRY = re.compile(r'\[\s*"wrb\.fr"\s*,\s*(?:"[^"]*"|null)\s*,\s*null')
GATED_MAX_BYTES = 1_000


def looks_gated(text: str) -> bool:
    """A reply with no prices that is Google declining, not a changed layout: an error
    envelope, a payload-less entry, or a body too small to hold any data.

    Since August 2026 other clients report this RPC answering so unless the page's own
    script adds a token (`x-goog-batchexecute-bgr`); on 2026-11-15 one reply from home
    was 363 bytes with no prices and the next, minutes later, had 31, and on 2026-11-16
    the cloud sandbox got the same reply (saved in tests/fixtures/live-20261116). Waiting
    helps; a parser fix would not."""
    body = (text or "").strip()
    return (
        "ErrorResponse" in body
        or _EMPTY_ENTRY.search(body) is not None
        or len(body) < GATED_MAX_BYTES
    )


def parse_calendar(text: str) -> list[DatePrice]:
    """All (depart, return, price) tuples in the response, in date order."""
    out: list[DatePrice] = []
    seen: set[tuple] = set()
    for payload in unwrap_batchexecute(text):
        _walk(payload, out, seen)
    out.sort(key=lambda d: (d.depart, d.ret or ""))
    return out
