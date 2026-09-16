"""Adapter over `fast-flights` — the only module that talks to the network.

Everything else in the system speaks `Offer`. Keeping the dependency behind
this seam means that when `fast-flights` breaks on a Google change (plan §11
rates this "high, eventually"), exactly one file needs attention, and the
offline test suite keeps working via `FakeSource`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from .config import Config, Route

# A normalized price outside this band almost certainly means the upstream
# units changed (dollars vs cents), not that a real fare was that extreme.
# We record it anyway — never silently drop data — but flag it loudly.
PLAUSIBLE_MIN_CENTS = 1_000        # $10
PLAUSIBLE_MAX_CENTS = 5_000_000    # $50,000

_MONEY = re.compile(r"[-+]?\d[\d,\s.]*")
_WS = re.compile(r"\s+")
MAX_ERROR_CHARS = 300


def tidy_error(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """Collapse an upstream error to one readable line.

    Proxy and HTTP failures arrive as multi-line responses; stored raw they
    turn `run_log.notes` and `flighttrack status` into a wall of headers.
    """
    one_line = _WS.sub(" ", str(text)).strip()
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


class SourceError(Exception):
    """Fetch failed in a way that may indicate blocking or rate limiting.

    Two of these in a row abort the run. Never retried inline.
    """


class NoResults(Exception):
    """The query succeeded but Google had no itineraries.

    Distinct from SourceError on purpose: an empty result for a far-future date
    is normal and must not be mistaken for a rate limit, or the fetcher will
    abort runs over routes that simply have no inventory yet.
    """


@dataclass
class Offer:
    """One itinerary from one fetch, normalized."""

    price_cents: int
    currency: str = "USD"
    airline: str | None = None
    stops: int | None = None
    duration_min: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def raw_json(self) -> str:
        return json.dumps(self.raw, default=str, separators=(",", ":"))


class Source(Protocol):
    def fetch(
        self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int
    ) -> list[Offer]: ...


# ---------------------------------------------------------------------------
# Price normalization
# ---------------------------------------------------------------------------

def normalize_price(value: Any, assume_major_units: bool = True) -> int:
    """Normalize an upstream price to integer cents.

    This is the ingestion boundary the plan (§3, risk 4) insists on: the raw
    display value is never stored as the price.

    `fast-flights` 3.1.0 types `Flights.price` as `int`, but whether that int
    is dollars or cents is NOT verifiable without a live call, and the sandbox
    this was built in cannot reach Google. So:

      * ints and floats are treated as MAJOR units (dollars) by default,
      * strings like "$811" or "US$1,234.50" are parsed and treated the same,
      * the original value is always kept in `Offer.raw` so that if the
        assumption turns out wrong, the entire history can be re-derived
        rather than re-collected.

    Run `flighttrack doctor` on the host that has network access: it prints the
    raw value beside the normalized one so the units can be confirmed on day
    one. If upstream ever returns minor units, pass assume_major_units=False.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"price is not a number: {value!r}")

    if isinstance(value, (int, float)):
        amount = float(value)
    elif isinstance(value, str):
        m = _MONEY.search(value)
        if not m:
            raise ValueError(f"no numeric component in price {value!r}")
        text = m.group(0).strip().replace(" ", "").replace(",", "")
        # A trailing ".00"-style group is a decimal; anything else was grouping.
        if text.count(".") > 1:
            text = text.replace(".", "")
        amount = float(text)
    else:
        raise ValueError(f"unsupported price type {type(value).__name__}: {value!r}")

    cents = round(amount * 100) if assume_major_units else round(amount)
    if cents <= 0:
        raise ValueError(f"non-positive price: {value!r}")
    return int(cents)


def price_is_plausible(cents: int) -> bool:
    """False if a price is so far outside normal that units likely changed."""
    return PLAUSIBLE_MIN_CENTS <= cents <= PLAUSIBLE_MAX_CENTS


# ---------------------------------------------------------------------------
# Query construction (offline — pure protobuf, no network)
# ---------------------------------------------------------------------------

def build_query(cfg: Config, route: Route, depart: str, ret: str | None):
    """Build a `fast-flights` Query. A round trip is two legs, not a return date."""
    from fast_flights import FlightQuery, Passengers, create_query

    legs = [
        FlightQuery(
            date=depart,
            from_airport=cfg.home,
            to_airport=route.dest,
            max_stops=route.max_stops,
        )
    ]
    if ret:
        legs.append(
            FlightQuery(
                date=ret,
                from_airport=route.dest,
                to_airport=cfg.home,
                max_stops=route.max_stops,
            )
        )

    return create_query(
        flights=legs,
        seat=cfg.search.seat,
        trip="round-trip" if ret else "one-way",
        passengers=Passengers(adults=cfg.search.adults),
        currency=cfg.search.currency,
        carry_on_bags=cfg.search.carry_on_bags,
        exclude_basic_economy=cfg.search.exclude_basic_economy,
        hide_separate_and_self_transfer=cfg.search.hide_separate_and_self_transfer,
    )


def deep_link(cfg: Config, route: Route, depart: str, ret: str | None) -> str:
    """The Google Flights URL for this exact search.

    Plan §12 is right that this is a small detail with an outsized effect: an
    alert you have to re-type by hand is an alert you ignore. `fast-flights`
    already builds the base64 protobuf, so this costs nothing and needs no
    network.
    """
    return build_query(cfg, route, depart, ret).url()


def _legs_count(ret: str | None) -> int:
    return 2 if ret else 1


def _to_offer(flight: Any, currency: str, n_legs: int) -> Offer:
    """Convert one upstream itinerary into our normalized Offer."""
    segments = list(getattr(flight, "flights", []) or [])

    # `flights` holds every segment across all legs, so total stops is segments
    # minus one arrival per leg. Verified shape is printed by `flighttrack
    # doctor`; the segment count is kept in `raw` either way.
    stops = max(0, len(segments) - n_legs) if segments else None

    durations = [getattr(s, "duration", None) for s in segments]
    durations = [d for d in durations if isinstance(d, int)]
    duration_min = sum(durations) if durations else None

    airlines = getattr(flight, "airlines", None) or []
    airline = "/".join(str(a) for a in airlines) if airlines else None

    raw_price = getattr(flight, "price", None)
    cents = normalize_price(raw_price)

    return Offer(
        price_cents=cents,
        currency=currency,
        airline=airline,
        stops=stops,
        duration_min=duration_min,
        raw={
            "price_raw": raw_price,
            "price_raw_type": type(raw_price).__name__,
            "type": getattr(flight, "type", None),
            "airlines": [str(a) for a in airlines],
            "segment_count": len(segments),
            "legs_requested": n_legs,
            "segment_durations": durations,
            "segments": [
                {
                    "from": getattr(getattr(s, "from_airport", None), "code", None),
                    "to": getattr(getattr(s, "to_airport", None), "code", None),
                    "plane": getattr(s, "plane_type", None),
                }
                for s in segments
            ],
        },
    )


class LiveSource:
    """The real thing: one HTTP round trip per call, never concurrent."""

    def fetch(
        self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int
    ) -> list[Offer]:
        from fast_flights import get_flights
        from fast_flights.exceptions import FlightsNotFound

        query = build_query(cfg, route, depart, ret)
        try:
            result = get_flights(query)
        except FlightsNotFound as exc:
            raise NoResults(tidy_error(exc) or "no itineraries returned") from exc
        except Exception as exc:
            # Network errors, proxy denials, parse failures on a changed page
            # shape — all treated as potentially-blocking and never retried
            # inline. The fetcher decides whether to abort the run.
            raise SourceError(tidy_error(f"{type(exc).__name__}: {exc}")) from exc

        offers = _offers_from_result(result, cfg.search.currency, _legs_count(ret), limit)
        if not offers:
            raise NoResults("result contained no parseable itineraries")
        return offers


def _offers_from_result(
    result: Iterable[Any], currency: str, n_legs: int, limit: int
) -> list[Offer]:
    """Normalize, drop unparseable entries, keep the cheapest `limit`."""
    offers: list[Offer] = []
    for flight in result:
        try:
            offers.append(_to_offer(flight, currency, n_legs))
        except (ValueError, TypeError):
            # One malformed itinerary must not lose the rest of the fetch.
            continue

    offers.sort(key=lambda o: o.price_cents)
    return offers[:limit]


class FakeSource:
    """Deterministic offline source, so the whole system is testable without network."""

    def __init__(self, prices_by_key: dict[tuple[str, str, str | None], list[int]] | None = None,
                 default: list[int] | None = None):
        self.prices = prices_by_key or {}
        self.default = default or [45000, 47500, 52000]
        self.calls: list[tuple[str, str, str | None]] = []

    def fetch(
        self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int
    ) -> list[Offer]:
        key = (route.dest, depart, ret)
        self.calls.append(key)
        prices = self.prices.get(key, self.default)
        if prices == "error":  # type: ignore[comparison-overlap]
            raise SourceError("synthetic failure")
        if not prices:
            raise NoResults("synthetic empty result")
        return [
            Offer(
                price_cents=p,
                currency=cfg.search.currency,
                airline="Alaska" if i == 0 else "Delta",
                stops=i,
                duration_min=360 + 30 * i,
                raw={"synthetic": True, "price_raw": p / 100},
            )
            for i, p in enumerate(sorted(prices)[:limit])
        ]
