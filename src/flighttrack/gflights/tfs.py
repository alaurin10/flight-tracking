"""Build the ``?tfs=`` parameter Google Flights reads its search from.

The parameter is a base64 protobuf. The message layout is not published, but
it is stable (it is what every Google Flights URL in the wild carries) and it
is tiny, so this module hand-encodes it with ~40 lines of wire-format code
rather than pulling in the `protobuf` runtime.

The field numbers below reproduce the layout used by `fast-flights` 3.1.0,
which is verified to work against live Google. The test suite asserts that
this encoder produces byte-identical output to that library for every
combination of options, so the two are interchangeable and this one has no
dependencies.

Wire format refresher (all we need):
    key     = varint((field_number << 3) | wire_type)
    varint  = 7 bits per byte, little-endian, MSB set on all but the last
    type 0  = varint (ints, bools, enums)
    type 2  = length-delimited (strings, nested messages, packed repeated)
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

SEATS = {"economy": 1, "premium-economy": 2, "business": 3, "first": 4}
TRIPS = {"round-trip": 1, "one-way": 2, "multi-city": 3}
PASSENGER = {"adult": 1, "child": 2, "infant_in_seat": 3, "infant_on_lap": 4}

DEFAULT_LANGUAGE = "en"
BASE_URL = "https://www.google.com/travel/flights/search"


# ---------------------------------------------------------------------------
# Wire-format primitives
# ---------------------------------------------------------------------------

def varint(n: int) -> bytes:
    if n < 0:
        # Negative int32 is sign-extended to 10 bytes in protobuf. We never
        # send one, but be correct rather than silently wrong.
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field_no: int, wire_type: int) -> bytes:
    return varint((field_no << 3) | wire_type)


def field_varint(field_no: int, value: int) -> bytes:
    return _key(field_no, 0) + varint(int(value))


def field_bytes(field_no: int, data: bytes) -> bytes:
    return _key(field_no, 2) + varint(len(data)) + data


def field_string(field_no: int, text: str) -> bytes:
    return field_bytes(field_no, text.encode("utf-8"))


def field_packed(field_no: int, values: list[int]) -> bytes:
    """proto3 `repeated` scalars are packed: one length-delimited blob of varints."""
    return field_bytes(field_no, b"".join(varint(v) for v in values))


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Leg:
    """One flight segment request: `origin` → `dest` on `date`."""

    date: str                       # 'YYYY-MM-DD'
    origin: str                     # IATA
    dest: str                       # IATA
    max_stops: int | None = None    # None = any
    airlines: tuple[str, ...] = ()  # restrict to these carriers
    earliest_departure_hour: int | None = None
    latest_departure_hour: int | None = None
    earliest_arrival_hour: int | None = None
    latest_arrival_hour: int | None = None
    max_duration_minutes: int | None = None
    connecting_airports: tuple[str, ...] = ()
    min_layover_minutes: int | None = None
    max_layover_minutes: int | None = None
    less_emissions_only: bool = False

    def encode(self) -> bytes:
        """`FlightData` message. Fields are emitted in field-number order, as
        the protobuf runtime does, so output is byte-identical to it."""
        out = bytearray()
        out += field_string(2, self.date)
        if self.max_stops is not None:
            out += field_varint(5, self.max_stops)
        for a in self.airlines:
            out += field_string(6, a)
        for no, v in (
            (8, self.earliest_departure_hour),
            (9, self.latest_departure_hour),
            (10, self.earliest_arrival_hour),
            (11, self.latest_arrival_hour),
            (12, self.max_duration_minutes),
        ):
            if v is not None:
                out += field_varint(no, v)
        out += field_bytes(13, field_string(2, self.origin))   # Airport{airport=2}
        out += field_bytes(14, field_string(2, self.dest))
        for c in self.connecting_airports:
            out += field_string(15, c)
        if self.min_layover_minutes is not None:
            out += field_varint(17, self.min_layover_minutes)
        if self.max_layover_minutes is not None:
            out += field_varint(18, self.max_layover_minutes)
        if self.less_emissions_only:
            out += field_packed(19, [1])
        return bytes(out)


@dataclass(frozen=True)
class Query:
    """A complete search: legs plus cabin, passengers, filters and display units."""

    legs: tuple[Leg, ...]
    seat: str = "economy"
    trip: str | None = None         # inferred from leg count when None
    adults: int = 1
    children: int = 0
    infants_in_seat: int = 0
    infants_on_lap: int = 0
    currency: str = "USD"
    language: str = DEFAULT_LANGUAGE
    max_price: int | None = None
    carry_on_bags: int = 0
    checked_bags: int = 0
    hide_separate_and_self_transfer: bool = False
    exclude_basic_economy: bool = False
    extra: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("a query needs at least one leg")
        if self.seat not in SEATS:
            raise ValueError(f"unknown seat {self.seat!r}; one of {sorted(SEATS)}")
        trip = self.trip or ("one-way" if len(self.legs) == 1 else "round-trip")
        if trip not in TRIPS:
            raise ValueError(f"unknown trip {trip!r}; one of {sorted(TRIPS)}")
        object.__setattr__(self, "trip", trip)
        total = self.adults + self.children + self.infants_in_seat + self.infants_on_lap
        if not 1 <= total <= 9:
            raise ValueError("passenger count must be between 1 and 9")
        if self.infants_on_lap > self.adults:
            raise ValueError("each infant on lap needs an adult")

    # -- construction helpers ------------------------------------------------

    @classmethod
    def round_trip(cls, origin: str, dest: str, depart: str, ret: str, **kw) -> "Query":
        ms = kw.pop("max_stops", None)
        return cls(
            legs=(Leg(depart, origin, dest, max_stops=ms), Leg(ret, dest, origin, max_stops=ms)),
            **kw,
        )

    @classmethod
    def one_way(cls, origin: str, dest: str, depart: str, **kw) -> "Query":
        ms = kw.pop("max_stops", None)
        return cls(legs=(Leg(depart, origin, dest, max_stops=ms),), **kw)

    # -- encoding --------------------------------------------------------------

    def passengers(self) -> list[int]:
        return (
            [PASSENGER["adult"]] * self.adults
            + [PASSENGER["child"]] * self.children
            + [PASSENGER["infant_in_seat"]] * self.infants_in_seat
            + [PASSENGER["infant_on_lap"]] * self.infants_on_lap
        )

    def encode(self) -> bytes:
        """`Info` message, fields in number order: 3, 8, 9, 12, 13, 17, 19, 25."""
        out = bytearray()
        for leg in self.legs:
            out += field_bytes(3, leg.encode())
        out += field_packed(8, self.passengers())
        out += field_varint(9, SEATS[self.seat])
        if self.max_price is not None:
            out += field_varint(12, self.max_price)
        if self.carry_on_bags or self.checked_bags:
            # Baggage{carry_on_bags=2, checked_bags=3}; both are `optional`, so
            # a zero is still written once the message exists.
            bag = field_varint(2, self.carry_on_bags) + field_varint(3, self.checked_bags)
            out += field_bytes(13, bag)
        if self.hide_separate_and_self_transfer:
            out += field_varint(17, 1)
        out += field_varint(19, TRIPS[self.trip])
        if self.exclude_basic_economy:
            out += field_varint(25, 1)
        return bytes(out)

    def tfs(self) -> str:
        """URL-safe base64 without padding — the form Google itself generates."""
        return base64.urlsafe_b64encode(self.encode()).decode("ascii").rstrip("=")

    def params(self) -> dict[str, str]:
        return {"tfs": self.tfs(), "hl": self.language, "curr": self.currency}

    def url(self) -> str:
        return f"{BASE_URL}?tfs={self.tfs()}&hl={self.language}&curr={self.currency}"

    def describe(self) -> str:
        legs = " / ".join(f"{l.origin}→{l.dest} {l.date}" for l in self.legs)
        return f"{legs} [{self.trip}, {self.seat}, {self.adults} adult(s)]"


def decode_tfs(text: str) -> bytes:
    """Decode either base64 flavour, with or without padding (used by tests/doctor)."""
    text = text.strip().replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    return base64.b64decode(text)
