"""Config loading and validation.

`config/flighttrack.yaml` (in `Settings.config_dir`) holds the tunables: search
filters, pacing, alerts, health, retention, the horizon, notification delivery
and the default deal feeds. It is optional; every key has a default in code.
What is tracked (trips, flexible searches, deal watches, feeds) lives in the
database and is edited through `watches.py`. Paths come from `Settings`.

`parse_watch_file` reads the old single-file shape (`home`, `routes`, `trips`,
`patterns`, `deals`) for `flighttrack import-config`.

Invalid config should fail loudly at load time, not at 3am inside a scheduled
run, so validation here is deliberately strict and the error messages name the
offending key.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .settings import Settings

log = logging.getLogger(__name__)

DOW = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
SEATS = {"economy", "premium-economy", "business", "first"}
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
    origin: str = ""  # empty = Config.home

    def origin_or(self, home: str) -> str:
        return self.origin or home


@dataclass(frozen=True)
class Pattern:
    """A recurring shape: 'every THU (or FRI) inside this window, N nights'."""

    name: str
    depart_dow: str  # first weekday, kept for backwards compatibility
    nights: int
    depart_dows: tuple[str, ...] = ()  # all weekdays; defaults to (depart_dow,)
    routes: tuple[str, ...] = ()  # empty = every route
    window_start: str | None = None  # ISO date; None = horizon
    window_end: str | None = None
    min_days_ahead: int | None = None  # per-pattern override

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
    # Optional in the file; written by export-config.
    origin: str | None = None
    title: str | None = None
    max_stops: int | None = None
    status: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class PatternEntry:
    """A pattern from the old file, with the optional keys export-config writes."""

    pattern: Pattern
    origin: str | None = None
    title: str | None = None
    target_price: int | None = None
    max_stops: int | None = None
    priority: int | None = None
    status: str | None = None


@dataclass(frozen=True)
class WatchFile:
    """The old single-file config's tracked part, for `import-config`."""

    home: str
    routes: list[Route]
    patterns: list[PatternEntry]
    trips: list[Trip]
    deal_watches: list[DealWatch]
    deal_feeds: list[str]
    deal_pages: list[str] = field(default_factory=list)  # web pages read as lists of posts


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
    refresh_hours: float = 6.0  # the worker prices each window again when it is this old
    flags_per_window: int = 2  # promising dates of each window sent on to a detail fetch
    max_calls_per_run: int = 12  # windows per one-off `flighttrack sweep` / `run`
    confirm_budget: int = 6  # detail fetches a one-off sweep spends confirming candidates
    priority_boost_pct: float = 10.0  # a sweep price this far under the 30d low earns a confirm


@dataclass(frozen=True)
class DealWatch:
    name: str
    origins: tuple[str, ...]  # words that must appear (any), e.g. Seattle, SEA, "West Coast"
    destinations: tuple[str, ...]  # region names or words (any); empty = anywhere
    max_price: int | None = None  # cents
    keywords: tuple[str, ...] = ()  # extra words that must ALL appear (rare)
    months: tuple[int, ...] = ()  # travel months (1-12) a post must mention, if it names any
    nights: int | None = None  # trip length Explore prices a region for; None = a week
    origin: str | None = None  # IATA Explore prices a region from; None = home


@dataclass(frozen=True)
class Deals:
    enabled: bool = False
    feeds: tuple[str, ...] = ()
    max_alerts_per_run: int = 5
    lookback_days: int = 3


@dataclass(frozen=True)
class Retention:
    """What `compact` may remove. The is_best price series is never touched."""

    enabled: bool = True
    runner_up_days: int = 90  # 2nd–5th cheapest offers per fetch, and sweep context rows
    attempts_days: int = 180  # per-request log and feed poll log; 0 = keep forever


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
class Explore:
    """Whole regions priced with Google's Explore call (D2). Each board is a region from
    an airport for a trip length, priced for every week ahead (`horizon`), each departure
    again when it is `refresh_hours` old. `boards` are the built-in "anywhere from home"
    boards, by trip length; region watches add their own."""

    enabled: bool = False
    refresh_hours: float = 24.0
    boards: tuple[int, ...] = (3, 7)  # nights; 0 = one way
    fresh_hours: float = 72.0  # a fare older than this is not shown on a board


@dataclass(frozen=True)
class Finds:
    """The deal finder (D3): Explore fares well under a place's usual price become
    candidates, confirmed by one real results-page search with the owner's filters."""

    enabled: bool = False
    under_usual_pct: float = 25.0  # a candidate is this far under the usual price
    new_low_pct: float = 10.0  # ...or this far under the lowest fare seen before
    usual_days: int = 45  # the usual price is the median daily low over these days
    min_history_days: int = 7  # days of fares before "under its usual" is claimed
    cheapest_per_board: int = 3  # with too little history: each board's cheapest few
    confirm_slack_pct: float = 30.0  # the real price may be this far over the estimate
    recheck_hours: float = 12.0  # a confirmed find is searched again this often
    renotify_drop_pct: float = 10.0  # alert again only when it drops this much more


@dataclass(frozen=True)
class Collect:
    """How the worker paces collecting. Every Google request counts against one rolling
    24-hour allowance that the worker learns (`pace.py`): it starts at
    `pace_start_per_day`, rises after clean busy days up to `pace_max_per_day`, and
    falls when Google blocks. `daily_request_budget` pins it to a fixed number instead.
    Requests go out a few at a time (`batch_size`), spread evenly over the day, and each
    slot goes to the lane (detail pages, calendar windows, explore) furthest under its
    share."""

    daily_request_budget: int | None = None  # set: a fixed allowance, nothing learned
    pace_start_per_day: int = 50
    pace_max_per_day: int = 720
    pace_step_up_pct: float = 50.0
    batch_size: int = 3
    min_gap_seconds: float = 60.0  # never closer than this between slots
    shares: tuple[int, int, int] = (60, 25, 15)  # detail, calendar, explore (percent)
    trip_refresh_hours: int = 4  # an active trip older than this goes first
    pair_refresh_hours: int = 12  # the rotation: a priority-1 pair is due again after this
    top_dates_per_search: int = 3  # each search's cheapest dates, refreshed like trips
    canary_dest: str = "LAX"  # the daily canary: home airport to here, one way, 30 days out


@dataclass(frozen=True)
class Config:
    home: str  # the default origin (HOME_AIRPORT)
    min_days_ahead: int
    max_days_ahead: int
    search: Search
    fetch: Fetch
    alerts: Alerts
    db_path: str = "data/flights.db"
    calendar: Calendar = field(default_factory=Calendar)
    deals: Deals = field(default_factory=Deals)
    health: Health = field(default_factory=Health)
    retention: Retention = field(default_factory=Retention)
    failure_dir: str = "data/failures"
    collect: Collect = field(default_factory=Collect)
    explore: Explore = field(default_factory=Explore)
    finds: Finds = field(default_factory=Finds)


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
    return str(code)


def _int(v: Any, where: str, lo: int | None = None, hi: int | None = None) -> int:
    _require(isinstance(v, int) and not isinstance(v, bool), f"{where} must be an integer")
    if lo == 0:
        _require(v >= 0, f"{where} must be a non-negative integer")
    elif lo is not None:
        _require(v >= lo, f"{where} must be >= {lo}")
    if hi is not None:
        _require(v <= hi, f"{where} must be <= {hi}")
    return int(v)


def _cents(v: Any, where: str) -> int | None:
    if v is None:
        return None
    _require(
        isinstance(v, int) and not isinstance(v, bool) and v > 0,
        f"{where} must be a positive integer in CENTS",
    )
    return int(v)


def _iso(v: Any, where: str) -> str:
    v = v.isoformat() if isinstance(v, date) else v  # YAML parses bare dates
    _require(isinstance(v, str) and bool(_ISO.match(v)), f"{where} must be a date YYYY-MM-DD")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ConfigError(f"{where}: {v} is not a valid date") from None
    return str(v)


def _str_list(v: Any, where: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        v = [v]
    _require(
        isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v),
        f"{where} must be a list of strings",
    )
    return tuple(x.strip() for x in v)


def _read_yaml(path: Path, what: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{what} {path} is not valid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{what} {path}: the root must be a mapping")
    return raw


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """`over` on top of `base`; mappings merge key by key, anything else is replaced."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def parse_watch_file(raw: dict[str, Any]) -> WatchFile:
    """Validate the old single-file shape: `home`, `routes`, `trips`, `patterns`, `deals`."""
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    raw = _expand_env(raw)
    home = _airport(raw.get("home"), "home")

    # --- routes -----------------------------------------------------------
    raw_routes = raw.get("routes") or []
    _require(isinstance(raw_routes, list), "routes: must be a list")
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
        _require(
            isinstance(label, str) and bool(label.strip()),
            f"{where}: label must be a non-empty string",
        )
        target = _cents(r.get("target_price"), f"{where}.target_price")
        priority = r.get("priority", 2)
        _require(priority in (1, 2, 3), f"{where}: priority must be 1, 2 or 3")
        max_stops = r.get("max_stops")
        if max_stops is not None:
            max_stops = _int(max_stops, f"{where}.max_stops", lo=0)
        routes.append(Route(dest, label.strip(), target, priority, max_stops, origin=home))

    # --- patterns ---------------------------------------------------------
    raw_patterns = raw.get("patterns") or []
    _require(isinstance(raw_patterns, list), "patterns: must be a list")
    patterns: list[PatternEntry] = []
    pat_names: set[str] = set()
    for i, p in enumerate(raw_patterns):
        where = f"patterns[{i}]"
        _require(isinstance(p, dict), f"{where}: must be a mapping")
        name = p.get("name")
        _require(
            isinstance(name, str) and bool(name.strip()),
            f"{where}: name must be a non-empty string",
        )
        _require(name not in pat_names, f"{where}: duplicate pattern name {name!r}")
        pat_names.add(name)

        dows_raw = p.get("depart_dow")
        dows = tuple(
            str(d).strip().upper() for d in (dows_raw if isinstance(dows_raw, list) else [dows_raw])
        )
        _require(
            bool(dows) and all(d in DOW for d in dows),
            f"{where}: depart_dow must be one or more of {sorted(DOW)}",
        )

        nights = _int(p.get("nights"), f"{where}.nights", lo=0)
        pat_routes = tuple(
            _airport(d, f"{where}.routes") for d in _str_list(p.get("routes"), f"{where}.routes")
        )
        for d in pat_routes:
            _require(d in seen, f"{where}.routes: {d} is not in routes")

        window = p.get("window") or {}
        _require(isinstance(window, dict), f"{where}.window must be a mapping with start/end")
        ws = (
            _iso(window["start"], f"{where}.window.start")
            if window.get("start") is not None
            else None
        )
        we = _iso(window["end"], f"{where}.window.end") if window.get("end") is not None else None
        if ws and we:
            _require(ws <= we, f"{where}.window: start is after end")
        mda = p.get("min_days_ahead")
        if mda is not None:
            mda = _int(mda, f"{where}.min_days_ahead", lo=0)

        pattern = Pattern(name, dows[0], nights, dows, pat_routes, ws, we, mda)
        patterns.append(
            PatternEntry(
                pattern,
                origin=_airport(p["origin"], f"{where}.origin") if p.get("origin") else None,
                title=_opt_str(p.get("title"), f"{where}.title"),
                target_price=_cents(p.get("target_price"), f"{where}.target_price"),
                max_stops=_opt_int(p.get("max_stops"), f"{where}.max_stops"),
                priority=_opt_int(p.get("priority"), f"{where}.priority"),
                status=_opt_str(p.get("status"), f"{where}.status"),
            )
        )

    # --- trips ------------------------------------------------------------
    raw_trips = raw.get("trips") or []
    _require(isinstance(raw_trips, list), "trips: must be a list")
    trips: list[Trip] = []
    for i, t in enumerate(raw_trips):
        where = f"trips[{i}]"
        _require(isinstance(t, dict), f"{where}: must be a mapping")
        name = t.get("name")
        _require(
            isinstance(name, str) and bool(name.strip()),
            f"{where}: name must be a non-empty string",
        )
        _require(name not in pat_names, f"{where}: name {name!r} collides with a pattern")
        pat_names.add(name)
        dest = _airport(t.get("dest"), where)
        _require(
            dest in seen,
            f"{where}: {dest} must also be listed under routes (it carries the label and priority)",
        )
        dep = _iso(t.get("depart"), f"{where}.depart")
        ret = _iso(t.get("return"), f"{where}.return") if t.get("return") is not None else None
        if ret:
            _require(ret > dep, f"{where}: return must be after depart")
        trips.append(
            Trip(
                name,
                dest,
                dep,
                ret,
                _cents(t.get("target_price"), f"{where}.target_price"),
                origin=_airport(t["origin"], f"{where}.origin") if t.get("origin") else None,
                title=_opt_str(t.get("title"), f"{where}.title"),
                max_stops=_opt_int(t.get("max_stops"), f"{where}.max_stops"),
                status=_opt_str(t.get("status"), f"{where}.status"),
                notes=_opt_str(t.get("notes"), f"{where}.notes"),
            )
        )

    deals_raw = raw.get("deals") or {}
    _require(isinstance(deals_raw, dict), "deals: must be a mapping")
    deal_watches: list[DealWatch] = []
    for i, w in enumerate(deals_raw.get("watches") or []):
        where = f"deals.watches[{i}]"
        _require(isinstance(w, dict), f"{where}: must be a mapping")
        wname = w.get("name")
        _require(
            isinstance(wname, str) and bool(wname.strip()),
            f"{where}: name must be a non-empty string",
        )
        deal_watches.append(
            DealWatch(
                name=wname.strip(),
                origins=_str_list(w.get("origins"), f"{where}.origins"),
                destinations=_str_list(w.get("destinations"), f"{where}.destinations"),
                max_price=_cents(w.get("max_price"), f"{where}.max_price"),
                keywords=_str_list(w.get("keywords"), f"{where}.keywords"),
                months=_months(w.get("months"), f"{where}.months"),
                nights=_opt_int(w.get("nights"), f"{where}.nights"),
                origin=None
                if w.get("origin") in (None, "")
                else _airport(w.get("origin"), f"{where}.origin"),
            )
        )
    feeds = list(_str_list(deals_raw.get("feeds"), "deals.feeds"))
    pages = list(_str_list(deals_raw.get("pages"), "deals.pages"))
    return WatchFile(home, routes, patterns, trips, deal_watches, feeds, pages)


def _months(value: object, where: str) -> tuple[int, ...]:
    """Travel months, 1-12 (a deal watch's `months`); missing means any month."""
    if value is None:
        return ()
    _require(
        isinstance(value, list)
        and all(isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12 for m in value),
        f"{where}: must be a list of month numbers from 1 to 12",
    )
    assert isinstance(value, list)
    return tuple(sorted(set(value)))


def read_watch_file(path: str | Path) -> WatchFile:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    return parse_watch_file(_read_yaml(path, "config file"))


def _opt_str(v: Any, where: str) -> str | None:
    if v is None:
        return None
    _require(isinstance(v, str), f"{where} must be a string")
    return str(v).strip() or None


def _opt_int(v: Any, where: str) -> int | None:
    return None if v is None else _int(v, where, lo=0)


WATCH_SECTIONS = ("home", "routes", "trips", "patterns")


def read_raw(path: str | Path | None, settings: Settings) -> dict[str, Any]:
    """The tunables file (``path``, default `<CONFIG_DIR>/flighttrack.yaml`), unvalidated."""
    tunables = Path(path) if path is not None else settings.tunables_path
    if path is not None and not tunables.exists():
        raise ConfigError(f"config file not found: {tunables}")
    raw = _read_yaml(tunables, "tunables file") if tunables.exists() else {}
    moved = [k for k in WATCH_SECTIONS if k in raw]
    if moved or "watches" in (raw.get("deals") or {}):
        raise ConfigError(
            f"{tunables}: {', '.join(moved) or 'deals.watches'} moved to the database; "
            "load them with `flighttrack import-config <file>`"
        )
    if "output" in raw:
        log.warning(
            "%s: the output: section is ignored; the database and pages live under DATA_DIR (%s)",
            tunables,
            settings.data_dir,
        )
        raw = {k: v for k, v in raw.items() if k != "output"}
    return raw


def load(
    path: str | Path | None = None,
    strict_secrets: bool = True,
    *,
    settings: Settings | None = None,
) -> Config:
    """Load and validate the tunables file.

    ``strict_secrets`` controls whether a missing delivery secret is fatal —
    True for commands that send, False for read-only/diagnostic ones.
    """
    settings = settings or Settings()
    raw = _expand_env(read_raw(path, settings))
    home = _airport(settings.home_airport, "HOME_AIRPORT")

    # --- horizon ----------------------------------------------------------
    horizon = raw.get("horizon") or {}
    _require(isinstance(horizon, dict), "horizon: must be a mapping")
    lo = horizon.get("min_days_ahead", 21)
    hi = horizon.get("max_days_ahead", 240)
    _require(
        isinstance(lo, int) and lo >= 0, "horizon.min_days_ahead must be a non-negative integer"
    )
    _require(
        isinstance(hi, int) and hi > lo,
        "horizon.max_days_ahead must be greater than min_days_ahead",
    )

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
        _require(
            name in SOURCES, f"fetch.sources: unknown source {name!r}; one of {sorted(SOURCES)}"
        )
    transport = str(f.get("transport", "auto"))
    _require(transport in TRANSPORTS, f"fetch.transport must be one of {sorted(TRANSPORTS)}")
    cooldown = f.get("cooldown_hours", list(Fetch.cooldown_hours))
    _require(
        isinstance(cooldown, list)
        and bool(cooldown)
        and all(isinstance(x, (int, float)) and x > 0 for x in cooldown),
        "fetch.cooldown_hours must be a non-empty list of positive numbers",
    )
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
        serpapi_key=(f.get("serpapi") or {}).get("api_key") or settings.serpapi_key or None,
        serpapi_url=(f.get("serpapi") or {}).get("base_url") or None,
    )
    _require(fetch.max_queries_per_run > 0, "fetch.max_queries_per_run must be positive")
    _require(
        0 <= fetch.min_sleep_seconds <= fetch.max_sleep_seconds,
        "fetch.min_sleep_seconds must be >= 0 and <= max_sleep_seconds",
    )
    _require(
        fetch.abort_after_consecutive_failures >= 1,
        "fetch.abort_after_consecutive_failures must be >= 1",
    )
    _require(fetch.offers_per_fetch >= 1, "fetch.offers_per_fetch must be >= 1")

    # --- calendar (experimental sweep) -------------------------------------
    c = raw.get("calendar") or {}
    _require(isinstance(c, dict), "calendar: must be a mapping")
    calendar = Calendar(
        enabled=bool(c.get("enabled", False)),
        refresh_hours=float(c.get("refresh_hours", Calendar.refresh_hours)),
        flags_per_window=_int(
            c.get("flags_per_window", Calendar.flags_per_window), "calendar.flags_per_window", lo=0
        ),
        max_calls_per_run=int(c.get("max_calls_per_run", 12)),
        confirm_budget=int(c.get("confirm_budget", 6)),
        priority_boost_pct=float(c.get("priority_boost_pct", 10.0)),
    )
    _require(calendar.refresh_hours > 0, "calendar.refresh_hours must be positive")

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

    # --- deals (the watches are in the database; these feeds are the defaults) ---
    d = raw.get("deals") or {}
    _require(isinstance(d, dict), "deals: must be a mapping")
    deals = Deals(
        enabled=bool(d.get("enabled", False)),
        feeds=_str_list(d.get("feeds"), "deals.feeds"),
        max_alerts_per_run=int(d.get("max_alerts_per_run", 5)),
        lookback_days=int(d.get("lookback_days", 3)),
    )

    # --- health -----------------------------------------------------------
    h = raw.get("health") or {}
    _require(isinstance(h, dict), "health: must be a mapping")
    health = Health(
        max_hours_without_success=int(h.get("max_hours_without_success", 36)),
        notify=bool(h.get("notify", True)),
        renotify_hours=int(h.get("renotify_hours", 24)),
        stale_fraction_warn=float(h.get("stale_fraction_warn", 0.5)),
    )

    # --- retention --------------------------------------------------------
    rt = raw.get("retention") or {}
    _require(isinstance(rt, dict), "retention: must be a mapping")
    retention = Retention(
        enabled=bool(rt.get("enabled", True)),
        runner_up_days=_int(rt.get("runner_up_days", 90), "retention.runner_up_days", lo=0),
        attempts_days=_int(rt.get("attempts_days", 180), "retention.attempts_days", lo=0),
    )

    # --- collect (the worker) ----------------------------------------------
    co = raw.get("collect") or {}
    _require(isinstance(co, dict), "collect: must be a mapping")
    for gone in ("batch_gap_minutes", "sweep_at"):
        if gone in co:
            log.warning(
                "collect.%s is ignored: the worker now spreads requests over the whole day "
                "at its learned pace (collect.pace)",
                gone,
            )
    pc = co.get("pace") or {}
    _require(isinstance(pc, dict), "collect.pace: must be a mapping")
    budget = co.get("daily_request_budget")
    sh = co.get("shares") or {}
    _require(
        isinstance(sh, dict) and set(sh) <= {"detail", "calendar", "explore"},
        "collect.shares must map detail, calendar and explore to percentages",
    )
    shares = (
        _int(sh.get("detail", Collect.shares[0]), "collect.shares.detail", lo=1),
        _int(sh.get("calendar", Collect.shares[1]), "collect.shares.calendar", lo=0),
        _int(sh.get("explore", Collect.shares[2]), "collect.shares.explore", lo=0),
    )
    collect = Collect(
        daily_request_budget=None
        if budget is None
        else _int(budget, "collect.daily_request_budget", lo=1),
        pace_start_per_day=_int(
            pc.get("start_per_day", Collect.pace_start_per_day),
            "collect.pace.start_per_day",
            lo=1,
        ),
        pace_max_per_day=_int(
            pc.get("max_per_day", Collect.pace_max_per_day), "collect.pace.max_per_day", lo=1
        ),
        pace_step_up_pct=float(pc.get("step_up_pct", Collect.pace_step_up_pct)),
        batch_size=_int(co.get("batch_size", Collect.batch_size), "collect.batch_size", lo=1),
        min_gap_seconds=float(co.get("min_gap_seconds", Collect.min_gap_seconds)),
        shares=shares,
        trip_refresh_hours=_int(
            co.get("trip_refresh_hours", Collect.trip_refresh_hours),
            "collect.trip_refresh_hours",
            lo=1,
        ),
        pair_refresh_hours=_int(
            co.get("pair_refresh_hours", Collect.pair_refresh_hours),
            "collect.pair_refresh_hours",
            lo=1,
        ),
        top_dates_per_search=_int(
            co.get("top_dates_per_search", Collect.top_dates_per_search),
            "collect.top_dates_per_search",
            lo=0,
        ),
        canary_dest=_airport(co.get("canary_dest", Collect.canary_dest), "collect.canary_dest"),
    )
    _require(
        collect.pace_start_per_day <= collect.pace_max_per_day,
        "collect.pace.start_per_day must not be above collect.pace.max_per_day",
    )
    _require(0 < collect.pace_step_up_pct <= 200, "collect.pace.step_up_pct must be 1 to 200")
    _require(collect.min_gap_seconds >= 0, "collect.min_gap_seconds must not be negative")

    # --- explore (whole regions, D2) ------------------------------------------
    ex = raw.get("explore") or {}
    _require(isinstance(ex, dict), "explore: must be a mapping")
    boards = ex.get("boards", list(Explore.boards))
    _require(
        isinstance(boards, list)
        and all(isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 30 for n in boards),
        "explore.boards must be a list of trip lengths in nights, 0 (one way) to 30",
    )
    explore = Explore(
        enabled=bool(ex.get("enabled", False)),
        refresh_hours=float(ex.get("refresh_hours", Explore.refresh_hours)),
        boards=tuple(dict.fromkeys(int(n) for n in boards)),
        fresh_hours=float(ex.get("fresh_hours", Explore.fresh_hours)),
    )
    _require(explore.refresh_hours > 0, "explore.refresh_hours must be positive")
    _require(explore.fresh_hours > 0, "explore.fresh_hours must be positive")

    # --- finds (the deal finder, D3) -------------------------------------------
    fd = raw.get("finds") or {}
    _require(isinstance(fd, dict), "finds: must be a mapping")
    finds = Finds(
        enabled=bool(fd.get("enabled", False)),
        under_usual_pct=float(fd.get("under_usual_pct", Finds.under_usual_pct)),
        new_low_pct=float(fd.get("new_low_pct", Finds.new_low_pct)),
        usual_days=_int(fd.get("usual_days", Finds.usual_days), "finds.usual_days", lo=7),
        min_history_days=_int(
            fd.get("min_history_days", Finds.min_history_days), "finds.min_history_days", lo=1
        ),
        cheapest_per_board=_int(
            fd.get("cheapest_per_board", Finds.cheapest_per_board), "finds.cheapest_per_board", lo=0
        ),
        confirm_slack_pct=float(fd.get("confirm_slack_pct", Finds.confirm_slack_pct)),
        recheck_hours=float(fd.get("recheck_hours", Finds.recheck_hours)),
        renotify_drop_pct=float(fd.get("renotify_drop_pct", Finds.renotify_drop_pct)),
    )
    _require(0 < finds.under_usual_pct < 100, "finds.under_usual_pct must be between 0 and 100")
    _require(0 <= finds.new_low_pct < 100, "finds.new_low_pct must be between 0 and 100")
    _require(finds.confirm_slack_pct >= 0, "finds.confirm_slack_pct must not be negative")
    _require(finds.recheck_hours > 0, "finds.recheck_hours must be positive")

    # --- notify (gone since M8: alerts go by browser push) --------------------
    if "notify" in raw:
        log.warning(
            "the notify: section is ignored: alerts are recorded in the app and pushed to "
            "the owner's devices once VAPID keys are set (`flighttrack push keys`)"
        )

    return Config(
        home=home,
        min_days_ahead=lo,
        max_days_ahead=hi,
        search=search,
        fetch=fetch,
        alerts=alerts,
        db_path=str(settings.database),
        calendar=calendar,
        deals=deals,
        health=health,
        retention=retention,
        failure_dir=str(settings.failure_dir),
        collect=collect,
        explore=explore,
        finds=finds,
    )
