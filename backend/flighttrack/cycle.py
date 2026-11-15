"""One full collecting cycle as a function: what `flighttrack run` does.

expand → sweep (if enabled) → fetch → deals → alert → health → compact.
The worker (scheduler.py) runs the same stages on their own schedules instead;
this stays as a one-off full cycle for debugging.

Stages are isolated: an exception in one is reported and the next still runs.
A broken notification channel must never stop history from accumulating, and
a blocked scraper must never hide a health alert.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from . import alert as alert_mod
from . import compact as compact_mod
from . import deals as deals_mod
from . import expand as expand_mod
from . import fetch as fetch_mod
from . import health as health_mod
from . import sweep as sweep_mod
from .config import Config
from .source import Source

log = logging.getLogger(__name__)

STAGES = ("expand", "sweep", "fetch", "deals", "alert", "health", "compact")


@dataclass
class StageResult:
    name: str
    rc: int = 0
    summary: str = ""
    error: str | None = None


@dataclass
class DailyResult:
    stages: list[StageResult] = field(default_factory=list)

    @property
    def rc(self) -> int:
        return max((s.rc for s in self.stages), default=0)

    def summary(self) -> str:
        return "; ".join(f"{s.name}: {s.error or s.summary or 'ok'}" for s in self.stages)


def fetch_exit_code(stats: fetch_mod.FetchStats) -> int:
    if stats.skipped:
        return 0
    if stats.blocked:
        return 1
    if stats.attempted and stats.succeeded == 0 and stats.empty == 0:
        return 1
    return 0


def run_daily(
    conn: sqlite3.Connection,
    cfg: Config,
    source_factory: Callable[[Config], Source],
    limit: int | None = None,
    dry_run: bool = False,
    verbose: bool = True,
    log: Callable[[str], None] = print,
) -> DailyResult:
    """expand → sweep (if enabled) → fetch → deals → alert → health → compact."""
    result = DailyResult()
    source: Source | None = None
    say = log

    def get_source() -> Source:
        nonlocal source
        if source is None:
            source = source_factory(cfg)
        return source

    def stage(name: str, fn: Callable[[], tuple[int, str]]) -> None:
        if verbose:
            say(f"\n== {name} ==" if say is print else f"== {name} ==")
        sr = StageResult(name)
        try:
            sr.rc, sr.summary = fn()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            sr.rc, sr.error = 1, f"{type(exc).__name__}: {exc}"
            say(f"[{name}] crashed: {sr.error}")
            if verbose:
                logging.getLogger(__name__).exception("stage %s crashed", name)
        result.stages.append(sr)

    def do_expand() -> tuple[int, str]:
        r = expand_mod.expand(conn, cfg)
        if verbose:
            say(f"[expand] {r.summary()}")
        return 0, r.summary()

    def do_sweep() -> tuple[int, str]:
        if not cfg.calendar.enabled:
            if verbose:
                say("[sweep] disabled (calendar.enabled: false)")
            return 0, "disabled"
        s = sweep_mod.run(conn, cfg, confirm_source=get_source(), verbose=verbose)
        if verbose:
            say(f"[sweep] {s.summary()}")
        return 0, s.summary()

    def do_fetch() -> tuple[int, str]:
        s = fetch_mod.run(conn, cfg, get_source(), limit=limit, verbose=verbose)
        if verbose:
            say(f"[fetch] {s.summary()}")
        return fetch_exit_code(s), s.summary()

    def do_deals() -> tuple[int, str]:
        s = deals_mod.run(conn, cfg, dry_run=dry_run, verbose=verbose)
        return (1 if s.delivery_error else 0), s.summary()

    def do_alert() -> tuple[int, str]:
        s = alert_mod.run(conn, cfg, dry_run=dry_run, verbose=verbose)
        return (1 if s.delivery_error else 0), s.summary()

    def do_health() -> tuple[int, str]:
        rep, _ = health_mod.run(conn, cfg, dry_run=dry_run, verbose=verbose)
        return (0 if rep.ok else 1), ("ok" if rep.ok else rep.signature)

    def do_compact() -> tuple[int, str]:
        if not compact_mod.due(conn, cfg):
            return 0, "not due"
        st = compact_mod.compact(conn, cfg)
        if verbose:
            say(f"[compact] {st.summary()}")
        return 0, st.summary()

    stage("expand", do_expand)
    stage("sweep", do_sweep)
    stage("fetch", do_fetch)
    stage("deals", do_deals)
    stage("alert", do_alert)
    stage("health", do_health)
    stage("compact", do_compact)
    return result
