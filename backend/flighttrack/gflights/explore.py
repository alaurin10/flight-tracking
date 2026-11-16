"""EXPERIMENTAL — the call behind Google Flights' Explore map (`GetExploreDestinations`).

Why it matters: one request prices every destination of a region (Anywhere, Europe,
Asia, …) from one origin for one departure date and trip length: about 75 to 90
places, roughly three quarters of them with a fare. It answers "where can I go
cheaply?" without naming the places first, which neither the results page nor the
calendar call can.

Where the layout comes from: the request and reply positions follow the notes in
`punitarani/fli` pull request 226 (MIT), which mapped them from a browser capture of
the Explore page in 2026-08, toggling one filter at a time. Positions the capture saw
change are used as such; the rest are sent as the capture sent them.

Like the calendar call it is an internal batchexecute endpoint: the reply is several
`wrb.fr` entries in no fixed order, some carrying destination records (name, country,
coordinates, dates) at `[3][0]`, others price records (fare, airline, stops, time,
airport) at `[4][0]`, joined on the knowledge-graph place id at `record[0]`. Google
sometimes answers it with an empty error-13 envelope instead (reported since 2026-09);
that is `looks_gated`, as for the calendar call.

Fares are Google's cheapest found for that date, with the bag filter applied but
basic economy included (no exclude-basic slot is known), so they are estimates, like
calendar prices, and only compared with each other.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .calendar import looks_gated, unwrap_batchexecute
from .tfs import SEATS

__all__ = [
    "PLACES",
    "RPC_HEADERS",
    "RPC_PARAMS",
    "RPC_URL",
    "ExploreDestination",
    "ExploreResult",
    "build_body",
    "looks_gated",
    "parse_explore",
]

RPC_URL = (
    "https://www.google.com/_/FlightsFrontendUi/data/"
    "travel.frontend.flights.FlightsFrontendService/GetExploreDestinations"
)
RPC_PARAMS = {"hl": "en", "gl": "US", "curr": "USD"}
# Same-origin signals the endpoint requires, as the Explore page sends them.
RPC_HEADERS = {
    "X-Same-Domain": "1",
    "Origin": "https://www.google.com",
    "Referer": "https://www.google.com/travel/explore",
}

# Knowledge-graph place ids the Explore map accepts as a destination, with type 6
# (region). Europe and Southern Europe were seen in the browser capture; the others
# come from Google's place autocomplete and were checked against the live call in
# 2026-08 (fli PR 226). An airport is (IATA, 0); a city (mid, 4).
PLACES: dict[str, tuple[str, int]] = {
    "anywhere": ("/m/02j71", 6),
    "europe": ("/m/02j9z", 6),
    "southern europe": ("/m/0250wj", 6),
    "asia": ("/m/0j0k", 6),
    "africa": ("/m/0dg3n1", 6),
    "north america": ("/m/059g4", 6),
    "south america": ("/m/06n3y", 6),
    "oceania": ("/m/05nrg", 6),
}

_KG = ("/m/", "/g/")


def place_token(place: str) -> tuple[str, int]:
    """A region name from `PLACES`, an IATA airport code, or a raw knowledge-graph id."""
    key = place.strip().lower()
    if key in PLACES:
        return PLACES[key]
    if place.startswith(_KG):
        return (place, 6)
    if len(place) == 3 and place.isalpha():
        return (place.upper(), 0)
    raise ValueError(f"unknown Explore place {place!r}; one of {sorted(PLACES)} or an airport")


def build_body(
    origin: str,
    place: str,
    depart: str,
    nights: int | None,
    *,
    seat: str = "economy",
    adults: int = 1,
    max_stops: int | None = None,
    carry_on_bags: int = 0,
    checked_bags: int = 0,
) -> str:
    """The `f.req=` form body. `nights=None` means one way; a round trip pins the trip
    length with the window `[4, 23, nights, nights]` (without it Google quotes one-way
    fares)."""
    round_trip = nights is not None
    stops = 0 if max_stops is None else min(3, max_stops + 1)  # 0 any, 1 nonstop, 2 ≤1, 3 ≤2
    dest_id, dest_type = place_token(place)
    slice_ = [
        [[[origin.upper(), 0]]],  # 0: origin [[[code, type]]]
        [[[dest_id, dest_type]]],  # 1: destination
        [4, 23, nights, nights] if round_trip else None,  # 2: trip-length window
        stops,  # 3: stops
        None,  # 4: airlines to include
        None,  # 5: airlines to leave out
        depart,  # 6: departure date
        None,  # 7: maximum duration, minutes
    ]
    bags = [carry_on_bags, checked_bags] if (carry_on_bags or checked_bags) else None
    inner: list[Any] = [None] * 26
    inner[2] = 1 if round_trip else 2  # trip type
    inner[4] = []
    inner[5] = SEATS[seat]
    inner[6] = [adults, 0, 0, 0]
    inner[10] = bags  # [carry_on, checked] (the reverse of nothing: same as the calendar)
    inner[13] = [slice_]
    inner[17] = inner[24] = inner[25] = 1
    outer = [[], None, None, inner, None, 1, None, 0, None, 0, [447, 712], 3]
    payload = json.dumps([None, json.dumps(outer, separators=(",", ":"))], separators=(",", ":"))
    return "f.req=" + urllib.parse.quote(payload, safe="") + "&"


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------


@dataclass
class ExploreDestination:
    place_id: str  # knowledge-graph id: the join key, and stable across replies
    name: str
    country: str | None = None
    lat: float | None = None
    lon: float | None = None
    depart: str | None = None
    price: int | float | None = None  # whole currency units; None = no fare found
    airline: str | None = None  # code of the cheapest itinerary's carrier
    airline_name: str | None = None
    stops: int | None = None
    duration_min: int | None = None
    airport: str | None = None  # the airport the cheapest itinerary lands at

    def return_date(self, nights: int | None) -> str | None:
        if nights is None or not self.depart:
            return None
        return (date.fromisoformat(self.depart) + timedelta(days=nights)).isoformat()


@dataclass
class ExploreResult:
    region_name: str | None = None
    origin_name: str | None = None
    destinations: list[ExploreDestination] = field(default_factory=list)

    @property
    def priced(self) -> list[ExploreDestination]:
        """Destinations with a fare, cheapest first."""
        return sorted((d for d in self.destinations if d.price), key=lambda d: d.price or 0)


def _at(node: Any, *path: int) -> Any:
    for i in path:
        if not isinstance(node, list) or not -len(node) <= i < len(node):
            return None
        node = node[i]
    return node


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _num(v: Any) -> int | float | None:
    return v if isinstance(v, int | float) and not isinstance(v, bool) else None


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _is_id(v: Any) -> bool:
    return isinstance(v, str) and v.startswith(_KG)


def parse_explore(text: str) -> ExploreResult:
    """Every destination in the reply, with its fare when Google found one. An empty
    result means the reply held no destination records (see `looks_gated`)."""
    result = ExploreResult()
    places: dict[str, ExploreDestination] = {}
    fares: dict[str, dict[str, Any]] = {}
    for chunk in unwrap_batchexecute(text):
        records = _at(chunk, 3, 0)
        for r in records if isinstance(records, list) else []:
            if not (isinstance(r, list) and _is_id(_at(r, 0)) and _str(_at(r, 2))):
                continue
            places.setdefault(
                r[0],
                ExploreDestination(
                    place_id=r[0],
                    name=r[2],
                    country=_str(_at(r, 4)),
                    lat=_num(_at(r, 1, 0)),
                    lon=_num(_at(r, 1, 1)),
                    depart=_str(_at(r, 11)),
                ),
            )
            result.region_name = result.region_name or _str(_at(chunk, 2, 0))
            result.origin_name = result.origin_name or _str(_at(chunk, 6, 0, 0))
        records = _at(chunk, 4, 0)
        for r in records if isinstance(records, list) else []:
            if not (isinstance(r, list) and _is_id(_at(r, 0))):
                continue
            price = _num(_at(r, 1, 0, 1))
            if price is None or price <= 0:
                continue  # Google keeps a row with no fare when nothing matched the filters
            duration = _int(_at(r, 6, 3))
            fares[r[0]] = {
                "price": price,
                "airline": _str(_at(r, 6, 0)),
                "airline_name": _str(_at(r, 6, 1)),
                "stops": _int(_at(r, 6, 2)),
                "duration_min": duration if duration and duration > 0 else None,
                "airport": _str(_at(r, 6, 5)),
            }
    for pid, d in places.items():
        for k, v in fares.get(pid, {}).items():
            setattr(d, k, v)
        result.destinations.append(d)
    return result
