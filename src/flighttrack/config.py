"""Config loading and validation.

Everything the system does is driven by one YAML file. Invalid config should
fail loudly at load time, not at 3am inside a cron job, so validation here is
deliberately strict and the error messages name the offending key.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

DOW = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
SEATS = {"economy", "premium-economy", "business", "first"}
CHANNELS = {"ntfy", "smtp", "file", "none"}
SOURCES = {"google_html", "fast_flights", "serpapi"}
TRANSPORTS = {"auto", "primp", "urllib"}
_IATA = re.compile(r"^[A-Z]{3}$")
_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ConfigError(Exception):
    """Raised for any malformed or self-contradictory configuration."""


@dataclass(frozen=True)
class Route:
    dest: str
    label: str
    target_price: int | None
    priority: int
    max_stops: int | None


@dataclass(frozen=True)
class Pattern:
    """A recurring shape: 'every THU (or FRI) inside this window, N nights'."""

    name: str
    depart_dow: str                         # first weekday, kept for backwards compatibility
    nights: int
    depart_dows: tuple[str, ...] = ()       # all weekdays; defaults to (depart_dow,)
    routes: tuple[str, ...] = ()            # empty = every route
    window_start: str | None = None         # ISO date; None = horizon
    window_end: str | None = None
    min_days_ahead: int | None = None       # per-pattern override

    def __post_init__(self) -> None:
        if not self.depart_dows:
            object.__setattr__(self, "depart_dows", (self.depart_dow,))

    @property
    def dow_index(self) -> int:
        return DOW[self.depart_dow]

    @property
    def dow_indexes(self) -> tuple[int, ...]:
        return tuple(DOW[d] for d in self.depart_dows)

    def applies_to(self, dest: str) -> bool:
        return not self.routes or dest in self.routes


@dataclass(frozen=True)
class Trip:
    """A fixed date pair you have already committed to — 'when should I book?'"""

    name: str
    dest: str
    depart: str
    ret: str | None
    target_price: int | None = None


@dataclass(frozen=True)
class Search:
    seat: str = "economy"
    currency: str = "USD"
    adults: int = 1
    carry_on_bags: int = 1
    exclude_basic_economy: bool = True
    hide_separate_and_self_transfer: bool = False


@dataclass(frozen=True)
class Fetch:
    max_queries_per_run: int = 60
    min_sleep_seconds: float = 4.0
    max_sleep_seconds: float = 8.0
    abort_after_consecutive_failures: int = 2
    offers_per_fetch: int = 5
    sources: tuple[str, ...] = ("google_html", "fast_flights")
    transport: str = "auto"
    retry_network_once: bool = True
    retry_sleep_seconds: float = 30.0
    cooldown_hours: tuple[float, ...] = (1.0, 4.0, 12.0, 24.0)
    keep_failure_artifacts: int = 20
    request_timeout_seconds: float = 30.0
    serpapi_key: str | None = None
    serpapi_url: str | None = None


@dataclass(frozen=True)
class Calendar:
    enabled: bool = False
    max_calls_per_run: int = 12
    confirm_budget: int = 6              # detail fetches spent confirming sweep candidates
    priority_boost_pct: float = 10.0     # a sweep price this far under the 30d low earns a confirm


@dataclass(frozen=True)
class DealWatch:
    name: str
    origins: tuple[str, ...]             # words that must appear (any), e.g. Seattle, SEA, "West Coast"
    destinations: tuple[str, ...]        # region names or words (any); empty = anywhere
    max_price: int | None = None         # cents
    keywords: tuple[str, ...] = ()       # extra words that must ALL appear (rare)


@dataclass(frozen=True)
class Deals:
    enabled: bool = False
    feeds: tuple[str, ...] = ()
    watches: tuple[DealWatch, ...] = ()
    max_alerts_per_run: int = 5
    lookback_days: int = 3


@dataclass(frozen=True)
class Health:
    max_hours_without_success: int = 36
    notify: bool = True
    renotify_hours: int = 24
    stale_fraction_warn: float = 0.5


@dataclass(frozen=True)
class Alerts:
    enabled: bool = True
    max_per_run: int = 3
    dedup_days: int = 7
    dedup_further_drop_pct: float = 10.0
    min_observations_for_all_time_low: int = 5
    percentile_phase2_enabled: bool = False
    percentile: int = 20
    percentile_window_days: int = 60
    history_days_required: int = 30
    consider_observations_within_hours: int = 48


@dataclass(frozen=True)
class Config:
    home: str
    routes: list[Route]
    patterns: list[Pattern]
    min_days_ahead: int
    max_days_ahead: int
    search: Search
    fetch: Fetch
    alerts: Alerts
    notify: dict[str, Any] = field(default_factory=dict)
    db_path: str = "data/flights.db"
    html_path: str = "out/index.html"
    trips: list[Trip] = field(default_factory=list)
    calendar: Calendar = field(default_factory=Calendar)
    deals: Deals = field(default_factory=Deals)
    health: Health = field(default_factory=Health)
    failure_dir: str = "data/failures"

    def route(self, dest: str) -> Route | None:
        for r in self.routes:
            if r.dest == dest:
                return r
        return None


# ---------------------------------------------------------------------------

def _expand_env(value: Any) -> Any:
    """Replace a whole-string ``${VAR}`` with its environment value."""
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        m = _ENV_REF.match(value.strip())
        if m:
            return os.environ.get(m.group(1))
    return value


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def _airport(code: Any, where: str) -> str:
    _require(isinstance(code, str), f"{where}: airport code must be a string")
    code = code.strip().upper()
    _require(bool(_IATA.match(code)), f"{where}: {code!r} is not a 3-letter IATA code")
    return code


def _int(v: Any, where: str, lo: int | None = None, hi: int | None = None) -> int:
    _require(isinstance(v, int) and not isinstance(v, bool), f"{where} must be an integer")
    if lo == 0:
        _require(v >= 0, f"{where} must be a non-negative integer")
    elif lo is not None:
        _require(v >= lo, f"{where} must be >= {lo}")
    if hi is not None:
        _require(v <= hi, f"{where} must be <= {hi}")
    return v


def _cents(v: Any, where: str) -> int | None:
    if v is None:
        return None
    _require(isinstance(v, int) and not isinstance(v, bool) and v > 0, f"{where} must be a positive integer in CENTS")
    return v


def _iso(v: Any, where: str) -> str:
    v = v.isoformat() if isinstance(v, date) else v   # YAML parses bare dates
    _require(isinstance(v, str) and bool(_ISO.match(v)), f"{where} must be a date YYYY-MM-DD")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ConfigError(f"{where}: {v} is not a valid date")
    return v


def _str_list(v: Any, where: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        v = [v]
    _require(isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v), f"{where} must be a list of strings")
    return tuple(x.strip() for x in v)


def load(path: str | Path, strict_secrets: bool = True) -> Config:
    """Load and validate the YAML config at ``path``.

    ``strict_secrets`` controls whether a missing delivery secret is fatal —
    True for commands that send, False for read-only/diagnostic ones.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    raw = _expand_env(raw)

    home = _airport(raw.get("home"), "home")

    # --- routes -----------------------------------------------------------
    raw_routes = raw.get("routes") or []
    _require(isinstance(raw_routes, list) and raw_routes, "routes: must be a non-empty list")
    routes: list[Route] = []
    seen: set[str] = set()
    for i, r in enumerate(raw_routes):
        where = f"routes[{i}]"
        _require(isinstance(r, dict), f"{where}: must be a mapping")
        dest = _airport(r.get("dest"), where)
        _require(dest != home, f"{where}: destination {dest} is the same as home")
        _require(dest not in seen, f"{where}: duplicate destination {dest}")
        seen.add(dest)
        label = r.get("label") or dest
        _require(isinstance(label, str) and label.strip(), f"{where}: label must be a non-empty string")
        target = _cents(r.get("target_price"), f"{where}.target_price")
        priority = r.get("priority", 2)
        _require(priority in (1, 2, 3), f"{where}: priority must be 1, 2 or 3")
        max_stops = r.get("max_stops")
        if max_stops is not None:
            max_stops = _int(max_stops, f"{where}.max_stops", lo=0)
        routes.append(Route(dest, label.strip(), target, priority, max_stops))

    # --- patterns ---------------------------------------------------------
    raw_patterns = raw.get("patterns") or []
    _require(isinstance(raw_patterns, list), "patterns: must be a list")
    patterns: list[Pattern] = []
    pat_names: set[str] = set()
    for i, p in enumerate(raw_patterns):
        where = f"patterns[{i}]"
        _require(isinstance(p, dict), f"{where}: must be a mapping")
        name = p.get("name")
        _require(isinstance(name, str) and name.strip(), f"{where}: name must be a non-empty string")
        _require(name not in pat_names, f"{where}: duplicate pattern name {name!r}")
        pat_names.add(name)

        dows_raw = p.get("depart_dow")
        dows = tuple(str(d).strip().upper() for d in (dows_raw if isinstance(dows_raw, list) else [dows_raw]))
        _require(bool(dows) and all(d in DOW for d in dows), f"{where}: depart_dow must be one or more of {sorted(DOW)}")

        nights = _int(p.get("nights"), f"{where}.nights", lo=0)
        pat_routes = tuple(_airport(d, f"{where}.routes") for d in _str_list(p.get("routes"), f"{where}.routes"))
        for d in pat_routes:
            _require(d in seen, f"{where}.routes: {d} is not in routes")

        window = p.get("window") or {}
        _require(isinstance(window, dict), f"{where}.window must be a mapping with start/end")
        ws = _iso(window["start"], f"{where}.window.start") if window.get("start") is not None else None
        we = _iso(window["end"], f"{where}.window.end") if window.get("end") is not None else None
        if ws and we:
            _require(ws <= we, f"{where}.window: start is after end")
        mda = p.get("min_days_ahead")
        if mda is not None:
            mda = _int(mda, f"{where}.min_days_ahead", lo=0)

        patterns.append(Pattern(name, dows[0], nights, dows, pat_routes, ws, we, mda))

    # --- trips ------------------------------------------------------------
    raw_trips = raw.get("trips") or []
    _require(isinstance(raw_trips, list), "trips: must be a list")
    trips: list[Trip] = []
    for i, t in enumerate(raw_trips):
        where = f"trips[{i}]"
        _require(isinstance(t, dict), f"{where}: must be a mapping")
        name = t.get("name")
        _require(isinstance(name, str) and name.strip(), f"{where}: name must be a non-empty string")
        _require(name not in pat_names, f"{where}: name {name!r} collides with a pattern")
        pat_names.add(name)
        dest = _airport(t.get("dest"), where)
        _require(dest in seen, f"{where}: {dest} must also be listed under routes (it carries the label and priority)")
        dep = _iso(t.get("depart"), f"{where}.depart")
        ret = _iso(t.get("return"), f"{where}.return") if t.get("return") is not None else None
        if ret:
            _require(ret > dep, f"{where}: return must be after depart")
        trips.append(Trip(name, dest, dep, ret, _cents(t.get("target_price"), f"{where}.target_price")))

    _require(bool(patterns or trips), "config needs at least one pattern or trip")

    # --- horizon ----------------------------------------------------------
    horizon = raw.get("horizon") or {}
    _require(isinstance(horizon, dict), "horizon: must be a mapping")
    lo = horizon.get("min_days_ahead", 21)
    hi = horizon.get("max_days_ahead", 240)
    _require(isinstance(lo, int) and lo >= 0, "horizon.min_days_ahead must be a non-negative integer")
    _require(isinstance(hi, int) and hi > lo, "horizon.max_days_ahead must be greater than min_days_ahead")

    # --- search -----------------------------------------------------------
    s = raw.get("search") or {}
    _require(isinstance(s, dict), "search: must be a mapping")
    seat = str(s.get("seat", "economy"))
    _require(seat in SEATS, f"search.seat must be one of {sorted(SEATS)}")
    adults = s.get("adults", 1)
    _require(isinstance(adults, int) and 1 <= adults <= 9, "search.adults must be between 1 and 9")
    search = Search(
        seat=seat,
        currency=str(s.get("currency", "USD")).upper(),
        adults=adults,
        carry_on_bags=int(s.get("carry_on_bags", 1)),
        exclude_basic_economy=bool(s.get("exclude_basic_economy", True)),
        hide_separate_and_self_transfer=bool(s.get("hide_separate_and_self_transfer", False)),
    )

    # --- fetch ------------------------------------------------------------
    f = raw.get("fetch") or {}
    _require(isinstance(f, dict), "fetch: must be a mapping")
    sources = _str_list(f.get("sources"), "fetch.sources") or Fetch.sources
    for name in sources:
        _require(name in SOURCES, f"fetch.sources: unknown source {name!r}; one of {sorted(SOURCES)}")
    transport = str(f.get("transport", "auto"))
    _require(transport in TRANSPORTS, f"fetch.transport must be one of {sorted(TRANSPORTS)}")
    cooldown = f.get("cooldown_hours", list(Fetch.cooldown_hours))
    _require(isinstance(cooldown, list) and cooldown and all(isinstance(x, (int, float)) and x > 0 for x in cooldown),
             "fetch.cooldown_hours must be a non-empty list of positive numbers")
    fetch = Fetch(
        max_queries_per_run=int(f.get("max_queries_per_run", 60)),
        min_sleep_seconds=float(f.get("min_sleep_seconds", 4.0)),
        max_sleep_seconds=float(f.get("max_sleep_seconds", 8.0)),
        abort_after_consecutive_failures=int(f.get("abort_after_consecutive_failures", 2)),
        offers_per_fetch=int(f.get("offers_per_fetch", 5)),
        sources=tuple(sources),
        transport=transport,
        retry_network_once=bool(f.get("retry_network_once", True)),
        retry_sleep_seconds=float(f.get("retry_sleep_seconds", 30.0)),
        cooldown_hours=tuple(float(x) for x in cooldown),
        keep_failure_artifacts=int(f.get("keep_failure_artifacts", 20)),
        request_timeout_seconds=float(f.get("request_timeout_seconds", 30.0)),
        serpapi_key=(f.get("serpapi") or {}).get("api_key") or None,
        serpapi_url=(f.get("serpapi") or {}).get("base_url") or None,
    )
    _require(fetch.max_queries_per_run > 0, "fetch.max_queries_per_run must be positive")
    _require(0 <= fetch.min_sleep_seconds <= fetch.max_sleep_seconds,
             "fetch.min_sleep_seconds must be >= 0 and <= max_sleep_seconds")
    _require(fetch.abort_after_consecutive_failures >= 1, "fetch.abort_after_consecutive_failures must be >= 1")
    _require(fetch.offers_per_fetch >= 1, "fetch.offers_per_fetch must be >= 1")

    # --- calendar (experimental sweep) -------------------------------------
    c = raw.get("calendar") or {}
    _require(isinstance(c, dict), "calendar: must be a mapping")
    calendar = Calendar(
        enabled=bool(c.get("enabled", False)),
        max_calls_per_run=int(c.get("max_calls_per_run", 12)),
        confirm_budget=int(c.get("confirm_budget", 6)),
        priority_boost_pct=float(c.get("priority_boost_pct", 10.0)),
    )

    # --- alerts -----------------------------------------------------------
    a = raw.get("alerts") or {}
    _require(isinstance(a, dict), "alerts: must be a mapping")
    alerts = Alerts(
        enabled=bool(a.get("enabled", True)),
        max_per_run=int(a.get("max_per_run", 3)),
        dedup_days=int(a.get("dedup_days", 7)),
        dedup_further_drop_pct=float(a.get("dedup_further_drop_pct", 10.0)),
        min_observations_for_all_time_low=int(a.get("min_observations_for_all_time_low", 5)),
        percentile_phase2_enabled=bool(a.get("percentile_phase2_enabled", False)),
        percentile=int(a.get("percentile", 20)),
        percentile_window_days=int(a.get("percentile_window_days", 60)),
        history_days_required=int(a.get("history_days_required", 30)),
        consider_observations_within_hours=int(a.get("consider_observations_within_hours", 48)),
    )
    _require(0 < alerts.percentile < 100, "alerts.percentile must be between 1 and 99")
    _require(alerts.max_per_run >= 1, "alerts.max_per_run must be >= 1")

    # --- deals ------------------------------------------------------------
    d = raw.get("deals") or {}
    _require(isinstance(d, dict), "deals: must be a mapping")
    watches: list[DealWatch] = []
    for i, w in enumerate(d.get("watches") or []):
        where = f"deals.watches[{i}]"
        _require(isinstance(w, dict), f"{where}: must be a mapping")
        wname = w.get("name")
        _require(isinstance(wname, str) and wname.strip(), f"{where}: name must be a non-empty string")
        watches.append(
            DealWatch(
                name=wname.strip(),
                origins=_str_list(w.get("origins"), f"{where}.origins"),
                destinations=_str_list(w.get("destinations"), f"{where}.destinations"),
                max_price=_cents(w.get("max_price"), f"{where}.max_price"),
                keywords=_str_list(w.get("keywords"), f"{where}.keywords"),
            )
        )
    deals = Deals(
        enabled=bool(d.get("enabled", False)),
        feeds=_str_list(d.get("feeds"), "deals.feeds"),
        watches=tuple(watches),
        max_alerts_per_run=int(d.get("max_alerts_per_run", 5)),
        lookback_days=int(d.get("lookback_days", 3)),
    )
    if deals.enabled:
        _require(bool(deals.feeds), "deals.enabled is true but deals.feeds is empty")
        _require(bool(deals.watches), "deals.enabled is true but deals.watches is empty")

    # --- health -----------------------------------------------------------
    h = raw.get("health") or {}
    _require(isinstance(h, dict), "health: must be a mapping")
    health = Health(
        max_hours_without_success=int(h.get("max_hours_without_success", 36)),
        notify=bool(h.get("notify", True)),
        renotify_hours=int(h.get("renotify_hours", 24)),
        stale_fraction_warn=float(h.get("stale_fraction_warn", 0.5)),
    )

    # --- notify -----------------------------------------------------------
    n = raw.get("notify") or {}
    _require(isinstance(n, dict), "notify: must be a mapping")
    channel = str(n.get("channel", "none"))
    _require(channel in CHANNELS, f"notify.channel must be one of {sorted(CHANNELS)}")
    if channel == "ntfy" and alerts.enabled and strict_secrets:
        topic = (n.get("ntfy") or {}).get("topic")
        _require(bool(topic), "notify.ntfy.topic is empty — set the NTFY_TOPIC environment variable "
                              "(or hard-code a topic) before enabling alerts")

    out = raw.get("output") or {}
    return Config(
        home=home,
        routes=routes,
        patterns=patterns,
        min_days_ahead=lo,
        max_days_ahead=hi,
        search=search,
        fetch=fetch,
        alerts=alerts,
        notify=n,
        db_path=str(out.get("db_path", "data/flights.db")),
        html_path=str(out.get("html_path", "out/index.html")),
        trips=trips,
        calendar=calendar,
        deals=deals,
        health=health,
        failure_dir=str(out.get("failure_dir", "data/failures")),
    )
