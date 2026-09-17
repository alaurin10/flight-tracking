"""The seam between the network and the rest of the system.

Everything outside this module speaks `Offer`. Inside, several *sources* can
produce offers, and a `ChainSource` tries them in order:

    google_html   our own client (gflights/) — default, no third-party scraper
    fast_flights  the `fast-flights` library, if installed — an independent
                  parser of the same page, so a bug in ours is not fatal
    fake          deterministic, offline, for tests

Failure is typed, because the fetcher must react differently to each:

    NoResults     Google answered; there is simply no inventory   → move on
    BlockedError  rate limited / captcha                          → abort run, cool off
    NetworkError  never reached Google, or Google 5xx             → retry once, then abort
    LayoutError   reached Google, got a page we cannot read       → try next source, flag
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config import Config, Route
from .gflights import PageKind, classify, itineraries
from .gflights.results import has_itinerary_lists
from .gflights.tfs import Leg, Query
from .gflights.transport import BASE_HEADERS, Response, Transport, TransportError, default_transport

# A normalized price outside this band almost certainly means the upstream
# units changed (dollars vs cents), not that a real fare was that extreme.
# We record it anyway — never silently drop data — but flag it loudly.
PLAUSIBLE_MIN_CENTS = 1_000        # $10
PLAUSIBLE_MAX_CENTS = 5_000_000    # $50,000

_MONEY = re.compile(r"[-+]?\d[\d,\s.]*")
_WS = re.compile(r"\s+")
MAX_ERROR_CHARS = 300

SEARCH_URL = "https://www.google.com/travel/flights/search"


def tidy_error(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """Collapse an upstream error to one readable line."""
    one_line = _WS.sub(" ", str(text)).strip()
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SourceError(Exception):
    """Fetch failed. Subclasses say how; the base class means 'unknown'."""

    kind = "unknown"


class BlockedError(SourceError):
    """Rate limited, captcha'd, or otherwise refused. Cool off before retrying."""

    kind = "blocked"


class NetworkError(SourceError):
    """Transport-level failure or an upstream 5xx. Transient; retry once."""

    kind = "network"


class LayoutError(SourceError):
    """We reached Google and got a page, but not one we know how to read."""

    kind = "layout"


class NoResults(Exception):
    """The query succeeded but Google had no itineraries.

    Distinct from SourceError on purpose: an empty result for a far-future
    date is normal and must not be mistaken for a rate limit.
    """


SEVERITY = {"blocked": 3, "network": 2, "layout": 1, "unknown": 0}


# ---------------------------------------------------------------------------
# Offer
# ---------------------------------------------------------------------------

@dataclass
class Offer:
    """One itinerary from one fetch, normalized."""

    price_cents: int
    currency: str = "USD"
    airline: str | None = None
    stops: int | None = None
    duration_min: int | None = None
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def raw_json(self) -> str:
        return json.dumps(self.raw, default=str, separators=(",", ":"))


class Source(Protocol):
    name: str

    def fetch(
        self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int
    ) -> list[Offer]: ...


# ---------------------------------------------------------------------------
# Price normalization
# ---------------------------------------------------------------------------

def normalize_price(value: Any, assume_major_units: bool = True) -> int:
    """Normalize an upstream price to integer cents.

    Google's data blob states prices as whole currency units (an $811 fare
    is the integer 811), so ints and floats are treated as MAJOR units. The
    original value is always kept in `Offer.raw`, so if that assumption ever
    breaks the history can be re-derived rather than re-collected.
    `flighttrack doctor` prints raw beside normalized so it can be eyeballed.
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
    return PLAUSIBLE_MIN_CENTS <= cents <= PLAUSIBLE_MAX_CENTS


# ---------------------------------------------------------------------------
# Query construction (offline — pure protobuf, no network, no dependencies)
# ---------------------------------------------------------------------------

def build_query(cfg: Config, route: Route, depart: str, ret: str | None) -> Query:
    legs = [Leg(depart, cfg.home, route.dest, max_stops=route.max_stops)]
    if ret:
        legs.append(Leg(ret, route.dest, cfg.home, max_stops=route.max_stops))
    return Query(
        legs=tuple(legs),
        seat=cfg.search.seat,
        adults=cfg.search.adults,
        currency=cfg.search.currency,
        carry_on_bags=cfg.search.carry_on_bags,
        exclude_basic_economy=cfg.search.exclude_basic_economy,
        hide_separate_and_self_transfer=cfg.search.hide_separate_and_self_transfer,
    )


def deep_link(cfg: Config, route: Route, depart: str, ret: str | None) -> str:
    """The Google Flights URL for this exact search — every alert carries one."""
    return build_query(cfg, route, depart, ret).url()


# ---------------------------------------------------------------------------
# Failure artifacts
# ---------------------------------------------------------------------------

class FailureStore:
    """Keep the last N raw responses that we could not read.

    When the page shape changes, the diagnosis is in the bytes Google sent,
    not in the exception message. Bounded so a broken week cannot fill a disk.
    """

    def __init__(self, directory: str | Path | None, keep: int = 20) -> None:
        self.dir = Path(directory) if directory else None
        self.keep = keep
        self._n = 0

    def save(self, kind: str, resp: Response, label: str = "") -> Path | None:
        if self.dir is None or self.keep <= 0:
            return None
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._n += 1
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", label)[:60]
            path = self.dir / f"{stamp}-{self._n:03d}-{kind}-{safe}.html"
            header = f"<!-- flighttrack failure artifact\nkind={kind}\nstatus={resp.status}\nurl={resp.url}\ntransport={resp.transport}\n-->\n"
            path.write_text(header + (resp.text or ""), encoding="utf-8")
            # Rotate: oldest first by name (timestamps sort lexically).
            files = sorted(self.dir.glob("*.html"))
            for old in files[: max(0, len(files) - self.keep)]:
                old.unlink(missing_ok=True)
            return path
        except OSError:
            return None


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class GoogleHtmlSource:
    """Our own client: one GET of the results page, classified and parsed."""

    name = "google_html"

    def __init__(
        self,
        transport: Transport | None = None,
        failures: FailureStore | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.transport = transport or default_transport()
        self.failures = failures or FailureStore(None)
        self.timeout = timeout
        self.last_page = None
        self.last_response: Response | None = None

    def fetch(self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int) -> list[Offer]:
        q = build_query(cfg, route, depart, ret)
        label = f"{cfg.home}-{route.dest}-{depart}"
        try:
            resp = self.transport.get(SEARCH_URL, params=q.params(), headers=BASE_HEADERS, timeout=self.timeout)
        except TransportError as exc:
            raise NetworkError(tidy_error(str(exc))) from exc
        self.last_response = resp

        page = classify(resp)
        self.last_page = page

        if page.kind is PageKind.NO_RESULTS:
            raise NoResults(page.detail)
        if page.kind is PageKind.BLOCKED:
            self.failures.save("blocked", resp, label)
            raise BlockedError(page.detail)
        if page.kind is PageKind.CONSENT:
            self.failures.save("consent", resp, label)
            raise LayoutError("consent interstitial — set a consent cookie or use a non-EU egress")
        if page.kind is PageKind.ERROR_STATUS:
            self.failures.save("status", resp, label)
            if resp.status >= 500:
                raise NetworkError(page.detail)
            raise LayoutError(page.detail)
        if page.kind is PageKind.LAYOUT_UNKNOWN:
            self.failures.save("layout", resp, label)
            raise LayoutError(page.detail)

        offers = offers_from_itineraries(itineraries(page.payload), cfg.search.currency, self.name, limit)
        if not offers:
            if has_itinerary_lists(page.payload):
                raise NoResults("results page had empty itinerary lists")
            self.failures.save("shape", resp, label)
            raise LayoutError("results payload decoded but no itinerary lists found at the expected positions")
        return offers


def offers_from_itineraries(its: list, currency: str, source: str, limit: int) -> list[Offer]:
    offers: list[Offer] = []
    for it in its:
        if it.price is None:
            continue
        try:
            cents = normalize_price(it.price)
        except ValueError:
            continue
        offers.append(
            Offer(
                price_cents=cents,
                currency=currency,
                airline="/".join(it.airline_names or it.airlines) or None,
                stops=it.stops,
                duration_min=it.duration_min,
                source=source,
                raw={
                    "price_raw": it.price,
                    "bucket": it.bucket,
                    "airlines": it.airlines,
                    "segments": [
                        {
                            "from": s.origin, "to": s.dest, "dep": f"{s.depart_date} {s.depart_time}",
                            "arr": f"{s.arrive_date} {s.arrive_time}", "min": s.duration_min,
                            "flight": f"{s.airline_code or ''}{s.flight_number or ''}" or None,
                            "aircraft": s.aircraft,
                        }
                        for s in it.segments
                    ],
                },
            )
        )
    offers.sort(key=lambda o: o.price_cents)
    return offers[:limit]


class FastFlightsSource:
    """The `fast-flights` library as an independent second parser of the same page.

    Optional. Its parser only reads Google's "best" list, so its minimum can
    sit above ours; that is fine for a fallback whose job is to keep history
    flowing while the primary is fixed.
    """

    name = "fast_flights"

    def __init__(self) -> None:
        import fast_flights  # noqa: F401 — ImportError surfaces at construction

    def fetch(self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int) -> list[Offer]:
        from fast_flights import FlightQuery, Passengers, create_query, get_flights
        from fast_flights.exceptions import FlightsNotFound

        legs = [FlightQuery(date=depart, from_airport=cfg.home, to_airport=route.dest, max_stops=route.max_stops)]
        if ret:
            legs.append(FlightQuery(date=ret, from_airport=route.dest, to_airport=cfg.home, max_stops=route.max_stops))
        query = create_query(
            flights=legs, seat=cfg.search.seat, trip="round-trip" if ret else "one-way",
            passengers=Passengers(adults=cfg.search.adults), currency=cfg.search.currency,
            carry_on_bags=cfg.search.carry_on_bags, exclude_basic_economy=cfg.search.exclude_basic_economy,
            hide_separate_and_self_transfer=cfg.search.hide_separate_and_self_transfer,
        )
        try:
            result = get_flights(query)
        except FlightsNotFound as exc:
            raise NoResults(tidy_error(exc) or "no itineraries returned") from exc
        except Exception as exc:
            raise classify_exception(exc) from exc

        offers: list[Offer] = []
        for f in result:
            try:
                segs = list(getattr(f, "flights", []) or [])
                offers.append(
                    Offer(
                        price_cents=normalize_price(getattr(f, "price", None)),
                        currency=cfg.search.currency,
                        airline="/".join(str(a) for a in (getattr(f, "airlines", None) or [])) or None,
                        # Outbound-leg segments only, whatever the trip type.
                        stops=max(0, len(segs) - 1) if segs else None,
                        duration_min=sum(d for d in (getattr(s, "duration", None) for s in segs) if isinstance(d, int)) or None,
                        source=self.name,
                        raw={"price_raw": getattr(f, "price", None), "segment_count": len(segs)},
                    )
                )
            except (ValueError, TypeError):
                continue
        offers.sort(key=lambda o: o.price_cents)
        if not offers:
            raise NoResults("result contained no parseable itineraries")
        return offers[:limit]


class SerpApiSource:
    """Google Flights results via SerpApi's `google_flights` engine.

    A paid, maintained JSON API that does the scraping on someone else's IP.
    It is the escape hatch: if your own IP is blocked or the page changes
    faster than we fix the parser, this keeps history flowing for a few
    dollars a month. Only assembled when an API key is configured.

    Prices are integers in the requested currency, same units as the page.
    """

    name = "serpapi"
    DEFAULT_URL = "https://serpapi.com/search.json"
    TRAVEL_CLASS = {"economy": 1, "premium-economy": 2, "business": 3, "first": 4}

    def __init__(self, api_key: str, base_url: str | None = None, transport: Transport | None = None,
                 timeout: float = 60.0) -> None:
        if not api_key:
            raise ValueError("serpapi source needs an api_key")
        self.api_key = api_key
        self.base_url = base_url or self.DEFAULT_URL
        self.transport = transport or default_transport("urllib")
        self.timeout = timeout

    def params(self, cfg: Config, route: Route, depart: str, ret: str | None) -> dict[str, str]:
        p = {
            "engine": "google_flights",
            "departure_id": cfg.home,
            "arrival_id": route.dest,
            "outbound_date": depart,
            "type": "1" if ret else "2",
            "travel_class": str(self.TRAVEL_CLASS[cfg.search.seat]),
            "adults": str(cfg.search.adults),
            "currency": cfg.search.currency,
            "hl": "en",
            "api_key": self.api_key,
        }
        if ret:
            p["return_date"] = ret
        if route.max_stops is not None:
            p["stops"] = str(min(3, route.max_stops + 1))     # 1 nonstop, 2 ≤1 stop, 3 ≤2 stops
        if cfg.search.carry_on_bags:
            p["bags"] = str(cfg.search.carry_on_bags)
        return p

    def fetch(self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int) -> list[Offer]:
        try:
            resp = self.transport.get(self.base_url, params=self.params(cfg, route, depart, ret), timeout=self.timeout)
        except TransportError as exc:
            raise NetworkError(tidy_error(str(exc))) from exc
        if resp.status == 429:
            raise BlockedError("serpapi: quota exhausted or rate limited (HTTP 429)")
        if resp.status >= 500:
            raise NetworkError(f"serpapi: HTTP {resp.status}")
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise LayoutError(f"serpapi: non-JSON response (HTTP {resp.status})") from exc
        if resp.status >= 400 or "error" in data:
            msg = tidy_error(str(data.get("error", f"HTTP {resp.status}")))
            if "no results" in msg.lower() or "hasn't returned any results" in msg.lower():
                raise NoResults(msg)
            raise LayoutError(f"serpapi: {msg}")
        return offers_from_serpapi(data, cfg.search.currency, self.name, limit)


def offers_from_serpapi(data: dict, currency: str, source: str, limit: int) -> list[Offer]:
    offers: list[Offer] = []
    for bucket in ("best_flights", "other_flights"):
        for it in data.get(bucket) or []:
            price = it.get("price")
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            legs = it.get("flights") or []
            airlines = list(dict.fromkeys(str(l.get("airline")) for l in legs if l.get("airline")))
            offers.append(
                Offer(
                    price_cents=normalize_price(price),
                    currency=currency,
                    airline="/".join(airlines) or None,
                    stops=max(0, len(legs) - 1) if legs else None,
                    duration_min=it.get("total_duration") if isinstance(it.get("total_duration"), int) else None,
                    source=source,
                    raw={
                        "price_raw": price, "bucket": bucket.split("_")[0],
                        "segments": [
                            {"from": (l.get("departure_airport") or {}).get("id"), "to": (l.get("arrival_airport") or {}).get("id"),
                             "flight": l.get("flight_number"), "min": l.get("duration"), "aircraft": l.get("airplane")}
                            for l in legs
                        ],
                    },
                )
            )
    if not offers and ("best_flights" in data or "other_flights" in data):
        raise NoResults("serpapi returned empty itinerary lists")
    if not offers:
        raise LayoutError("serpapi response had no best_flights/other_flights keys")
    offers.sort(key=lambda o: o.price_cents)
    return offers[:limit]


def classify_exception(exc: Exception) -> SourceError:
    """Best-effort typing of an arbitrary library exception."""
    text = tidy_error(f"{type(exc).__name__}: {exc}")
    low = text.lower()
    if any(s in low for s in ("429", "too many requests", "sorry/index", "unusual traffic", "captcha")):
        return BlockedError(text)
    if any(s in low for s in ("timed out", "timeout", "connection", "resolve", "proxy", "tls", "ssl", "eof", "reset")):
        return NetworkError(text)
    if any(s in low for s in ("attributeerror", "indexerror", "typeerror", "keyerror", "nonetype", "json")):
        return LayoutError(text)
    return SourceError(text)


class ChainSource:
    """Try sources in order. Failover only where it can help.

    * NoResults is an answer, not a failure — returned from the first source.
    * BlockedError stops the chain: every source hits the same IP, and a
      second request into a rate limit only lengthens it.
    * NetworkError / LayoutError fall through to the next source. If all
      fail, the most severe error is raised with every attempt's message.
    """

    def __init__(self, sources: list[Source]) -> None:
        if not sources:
            raise ValueError("ChainSource needs at least one source")
        self.sources = sources
        self.name = "chain(" + ">".join(s.name for s in sources) + ")"
        self.last_used: str | None = None
        self.last_errors: list[tuple[str, SourceError]] = []

    def fetch(self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int) -> list[Offer]:
        self.last_errors = []
        for src in self.sources:
            try:
                offers = src.fetch(cfg, route, depart, ret, limit)
                self.last_used = src.name
                return offers
            except NoResults:
                self.last_used = src.name
                raise
            except BlockedError as exc:
                self.last_errors.append((src.name, exc))
                raise
            except SourceError as exc:
                self.last_errors.append((src.name, exc))
                continue
        worst = max(self.last_errors, key=lambda e: SEVERITY.get(e[1].kind, 0))[1]
        msg = "; ".join(f"{n}: {e}" for n, e in self.last_errors)
        raise type(worst)(tidy_error(msg))


def build_source(cfg: Config, failure_dir: str | Path | None = None, transport: Transport | None = None) -> Source:
    """Assemble the chain named in `fetch.sources`."""
    failures = FailureStore(failure_dir, keep=cfg.fetch.keep_failure_artifacts)
    built: list[Source] = []
    for name in cfg.fetch.sources:
        if name == "google_html":
            built.append(GoogleHtmlSource(transport=transport or default_transport(cfg.fetch.transport), failures=failures))
        elif name == "fast_flights":
            try:
                built.append(FastFlightsSource())
            except ImportError:
                # Optional dependency absent: skip silently unless it is the only source.
                if len(cfg.fetch.sources) == 1:
                    raise
        elif name == "serpapi":
            if cfg.fetch.serpapi_key:
                built.append(SerpApiSource(cfg.fetch.serpapi_key, cfg.fetch.serpapi_url, transport=transport))
            elif len(cfg.fetch.sources) == 1:
                raise ValueError("fetch.sources is only 'serpapi' but fetch.serpapi.api_key is unset")
        else:
            raise ValueError(f"unknown source {name!r} in fetch.sources")
    return ChainSource(built) if len(built) != 1 else built[0]


# ---------------------------------------------------------------------------
# Offline source for tests
# ---------------------------------------------------------------------------

_SYNTHETIC = {
    "error": SourceError,
    "blocked": BlockedError,
    "network": NetworkError,
    "layout": LayoutError,
}


class FakeSource:
    """Deterministic offline source, so the whole system is testable without network.

    `prices_by_key[(dest, depart, ret)]` may be a list of cents, an empty list
    (NoResults), or one of the strings 'error' | 'blocked' | 'network' |
    'layout' to raise that failure.
    """

    name = "fake"

    def __init__(self, prices_by_key=None, default: list[int] | None = None):
        self.prices = prices_by_key or {}
        self.default = default or [45000, 47500, 52000]
        self.calls: list[tuple[str, str, str | None]] = []

    def fetch(self, cfg: Config, route: Route, depart: str, ret: str | None, limit: int) -> list[Offer]:
        key = (route.dest, depart, ret)
        self.calls.append(key)
        prices = self.prices.get(key, self.default)
        if isinstance(prices, str):
            raise _SYNTHETIC[prices](f"synthetic {prices}")
        if not prices:
            raise NoResults("synthetic empty result")
        return [
            Offer(
                price_cents=p,
                currency=cfg.search.currency,
                airline="Alaska" if i == 0 else "Delta",
                stops=i,
                duration_min=360 + 30 * i,
                source=self.name,
                raw={"synthetic": True, "price_raw": p / 100},
            )
            for i, p in enumerate(sorted(prices)[:limit])
        ]
