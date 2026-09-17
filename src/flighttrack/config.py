"""Config loading and validation.

Everything the system does is driven by one YAML file. Invalid config should
fail loudly at load time, not at 3am inside a cron job, so validation here is
deliberately strict and the error messages name the offending key.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DOW = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
SEATS = {"economy", "premium-economy", "business", "first"}
CHANNELS = {"ntfy", "smtp", "file", "none"}
_IATA = re.compile(r"^[A-Z]{3}$")
_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


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
    name: str
    depart_dow: str
    nights: int

    @property
    def dow_index(self) -> int:
        return DOW[self.depart_dow]


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


def _expand_env(value: Any) -> Any:
    """Replace a whole-string ``${VAR}`` with its environment value.

    Only exact ``${VAR}`` strings are substituted, so nothing else in the file
    is at risk of accidental expansion. An unset variable becomes None rather
    than the literal text, which lets validation report it as missing.
    """
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


def load(path: str | Path, strict_secrets: bool = True) -> Config:
    """Load and validate the YAML config at ``path``.

    ``strict_secrets`` controls whether a missing delivery secret is fatal. It is
    True for the command that actually sends (``alert``), so a misconfiguration
    fails loudly rather than at 3am inside cron. It is False for read-only and
    diagnostic commands — refusing to run ``doctor`` because a notification topic
    is unset would block the very tool you reach for to find out what is wrong.
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

        target = r.get("target_price")
        if target is not None:
            _require(
                isinstance(target, int) and not isinstance(target, bool) and target > 0,
                f"{where}: target_price must be a positive integer in CENTS",
            )

        priority = r.get("priority", 2)
        _require(priority in (1, 2, 3), f"{where}: priority must be 1, 2 or 3")

        max_stops = r.get("max_stops")
        if max_stops is not None:
            _require(
                isinstance(max_stops, int) and not isinstance(max_stops, bool) and max_stops >= 0,
                f"{where}: max_stops must be a non-negative integer or null",
            )

        routes.append(Route(dest, label.strip(), target, priority, max_stops))

    # --- patterns ---------------------------------------------------------
    raw_patterns = raw.get("patterns") or []
    _require(isinstance(raw_patterns, list) and raw_patterns, "patterns: must be a non-empty list")
    patterns: list[Pattern] = []
    pat_names: set[str] = set()
    for i, p in enumerate(raw_patterns):
        where = f"patterns[{i}]"
        _require(isinstance(p, dict), f"{where}: must be a mapping")
        name = p.get("name")
        _require(isinstance(name, str) and name.strip(), f"{where}: name must be a non-empty string")
        _require(name not in pat_names, f"{where}: duplicate pattern name {name!r}")
        pat_names.add(name)

        dow = str(p.get("depart_dow", "")).strip().upper()
        _require(dow in DOW, f"{where}: depart_dow must be one of {sorted(DOW)}")

        nights = p.get("nights")
        _require(
            isinstance(nights, int) and not isinstance(nights, bool) and nights >= 0,
            f"{where}: nights must be a non-negative integer (0 means one-way)",
        )
        patterns.append(Pattern(name, dow, nights))

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
    fetch = Fetch(
        max_queries_per_run=int(f.get("max_queries_per_run", 60)),
        min_sleep_seconds=float(f.get("min_sleep_seconds", 4.0)),
        max_sleep_seconds=float(f.get("max_sleep_seconds", 8.0)),
        abort_after_consecutive_failures=int(f.get("abort_after_consecutive_failures", 2)),
        offers_per_fetch=int(f.get("offers_per_fetch", 5)),
    )
    _require(fetch.max_queries_per_run > 0, "fetch.max_queries_per_run must be positive")
    _require(
        0 <= fetch.min_sleep_seconds <= fetch.max_sleep_seconds,
        "fetch.min_sleep_seconds must be >= 0 and <= max_sleep_seconds",
    )
    _require(fetch.abort_after_consecutive_failures >= 1, "fetch.abort_after_consecutive_failures must be >= 1")
    _require(fetch.offers_per_fetch >= 1, "fetch.offers_per_fetch must be >= 1")

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

    # --- notify -----------------------------------------------------------
    n = raw.get("notify") or {}
    _require(isinstance(n, dict), "notify: must be a mapping")
    channel = str(n.get("channel", "none"))
    _require(channel in CHANNELS, f"notify.channel must be one of {sorted(CHANNELS)}")
    if channel == "ntfy" and alerts.enabled and strict_secrets:
        topic = (n.get("ntfy") or {}).get("topic")
        _require(
            bool(topic),
            "notify.ntfy.topic is empty — set the NTFY_TOPIC environment variable "
            "(or hard-code a topic) before enabling alerts",
        )

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
    )
