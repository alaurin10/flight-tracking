"""Itineraries out of the ds:1 payload.

The payload is a positional JSON array with no schema. The indices below are
the ones observed in the wild (and used by `fast-flights`); every access goes
through `_at`, which returns None instead of raising, so a shifted index
degrades one field rather than losing the whole fetch. `Itinerary.raw` keeps
the untouched source row so anything missed can be re-derived later.

Two lists matter:
    payload[3][0]  "top departing flights"   — Google's best-ranked
    payload[2][0]  "other departing flights" — usually where the cheapest hides

Reading only the first (as fast-flights 3.1.0 does) systematically overstates
the minimum fare. We read both.

For a round trip, Google's results page lists OUTBOUND itineraries priced at
the round-trip total (the return is chosen on a second page). So `segments`
covers the outbound leg only and `stops = len(segments) - 1`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


def _at(obj: Any, *idx: int) -> Any:
    """Nested positional access that returns None on any miss."""
    for i in idx:
        if not isinstance(obj, (list, tuple)) or i >= len(obj) or i < -len(obj):
            return None
        obj = obj[i]
    return obj


def _time(pair: Any) -> str | None:
    """Google drops zero components: [8] = 08:00, [None, 31] = 00:31."""
    if pair is None:
        return None
    if not isinstance(pair, (list, tuple)):
        return None
    h = pair[0] if len(pair) > 0 and pair[0] is not None else 0
    m = pair[1] if len(pair) > 1 and pair[1] is not None else 0
    try:
        return f"{int(h):02d}:{int(m):02d}"
    except (TypeError, ValueError):
        return None


def _date(triple: Any) -> str | None:
    if isinstance(triple, (list, tuple)) and len(triple) >= 3:
        try:
            return f"{int(triple[0]):04d}-{int(triple[1]):02d}-{int(triple[2]):02d}"
        except (TypeError, ValueError):
            return None
    return None


@dataclass
class Segment:
    origin: str | None
    dest: str | None
    depart_date: str | None
    depart_time: str | None
    arrive_date: str | None
    arrive_time: str | None
    duration_min: int | None
    aircraft: str | None
    airline_code: str | None = None
    flight_number: str | None = None


@dataclass
class Itinerary:
    price: int | float | None          # as Google states it — whole currency units
    airlines: list[str]                # codes, e.g. ['AS', 'JL']
    airline_names: list[str]
    segments: list[Segment]
    bucket: str                        # 'best' | 'other'
    raw: Any = field(default=None, repr=False)

    @property
    def stops(self) -> int | None:
        return max(0, len(self.segments) - 1) if self.segments else None

    @property
    def duration_min(self) -> int | None:
        ds = [s.duration_min for s in self.segments if isinstance(s.duration_min, int)]
        return sum(ds) if ds else None

    def key(self) -> tuple:
        return (
            self.price,
            tuple((s.origin, s.dest, s.depart_date, s.depart_time, s.flight_number) for s in self.segments),
        )


def airline_directory(payload: Any) -> dict[str, str]:
    """`payload[7][1][1]` is a list of [code, name] pairs."""
    out: dict[str, str] = {}
    for pair in _at(payload, 7, 1, 1) or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2 and isinstance(pair[0], str):
            out[pair[0]] = str(pair[1])
    return out


def _segment(s: Any) -> Segment:
    return Segment(
        origin=_at(s, 3),
        dest=_at(s, 6),
        depart_date=_date(_at(s, 20)),
        depart_time=_time(_at(s, 8)),
        arrive_date=_date(_at(s, 21)),
        arrive_time=_time(_at(s, 10)),
        duration_min=_at(s, 11) if isinstance(_at(s, 11), int) else None,
        aircraft=_at(s, 17) if isinstance(_at(s, 17), str) else None,
        airline_code=_at(s, 22, 0) if isinstance(_at(s, 22, 0), str) else None,
        flight_number=_at(s, 22, 1) if isinstance(_at(s, 22, 1), str) else None,
    )


def _looks_like_itinerary(item: Any) -> bool:
    return (
        isinstance(item, (list, tuple))
        and len(item) >= 2
        and isinstance(_at(item, 0), (list, tuple))
        and isinstance(_at(item, 0, 2), (list, tuple))
    )


def _one(item: Any, bucket: str, names: dict[str, str]) -> Itinerary | None:
    flight = _at(item, 0)
    price = _at(item, 1, 0, 1)
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
        price = None
    codes = [c for c in (_at(flight, 1) or []) if isinstance(c, str)]
    segs = [_segment(s) for s in (_at(flight, 2) or []) if isinstance(s, (list, tuple))]
    if not segs and price is None:
        return None
    return Itinerary(
        price=price,
        airlines=codes,
        airline_names=[names.get(c, c) for c in codes],
        segments=segs,
        bucket=bucket,
        raw=item,
    )


def itineraries(payload: Any) -> list[Itinerary]:
    """Every itinerary on the page, both buckets, de-duplicated, cheapest first."""
    names = airline_directory(payload)
    out: list[Itinerary] = []
    seen: set[tuple] = set()
    for idx, bucket in ((3, "best"), (2, "other")):
        rows: Iterable[Any] = _at(payload, idx, 0) or []
        if not isinstance(rows, (list, tuple)):
            continue
        for item in rows:
            if not _looks_like_itinerary(item):
                continue
            it = _one(item, bucket, names)
            if it is None:
                continue
            k = it.key()
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
    out.sort(key=lambda i: (i.price is None, i.price or 0))
    return out


def has_itinerary_lists(payload: Any) -> bool:
    """True if the payload has the containers we expect, even if empty."""
    return isinstance(payload, (list, tuple)) and len(payload) > 3 and (
        isinstance(_at(payload, 3), (list, tuple)) or isinstance(_at(payload, 2), (list, tuple))
    )
