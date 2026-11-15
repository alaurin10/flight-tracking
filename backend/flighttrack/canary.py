"""The daily canary: one search whose answer we know the shape of.

A busy nonstop route from the home airport (`collect.canary_dest`, default LAX),
one way, 30 days out, always has a full results page. If the page stops looking
like one (too few itineraries, a list missing, prices out of range, the page's own
cheapest price disagreeing with ours, fields coming back empty), Google has most
likely changed the layout, and the tolerant parser would otherwise go on quietly
returning less. A failure saves the page, records `layout_drift` in `state` (which
`health.check` reports as critical) and a pass clears it.

A block or a network error says nothing about the layout: it leaves the verdict
as it was. A block still starts the cooldown, like any other blocked request.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import fetch as fetch_mod
from .config import Config, Route
from .gflights import PageKind
from .gflights.results import _at, itineraries, page_min_price
from .gflights.transport import Transport
from .health import STATE_LAYOUT_DRIFT
from .source import (
    BlockedError,
    FailureStore,
    GoogleHtmlSource,
    NoResults,
    SourceError,
    normalize_price,
    price_is_plausible,
)
from .store.db import iso, set_state, utcnow
from .store.db import now as clock_now

log = logging.getLogger(__name__)

SOURCE_NAME = "canary"
STATE_LAST_CANARY = "last_canary"  # {"at", "result": pass|fail|inconclusive, "detail"}
DAYS_AHEAD = 30
MIN_ITINERARIES = 5
MIN_COMPLETE_FRACTION = 0.8


@dataclass
class CanaryResult:
    result: str  # 'pass' | 'fail' | 'inconclusive'
    reasons: list[str] = field(default_factory=list)
    itineraries: int = 0
    saved: str | None = None

    @property
    def passed(self) -> bool:
        return self.result == "pass"

    def summary(self) -> str:
        s = f"{self.result} ({self.itineraries} itineraries)"
        if self.reasons:
            s += ": " + "; ".join(self.reasons)
        return s


def judge(payload: object) -> tuple[list[str], int]:
    """What is wrong with a results payload, as reasons a person can read. Empty = pass."""
    reasons: list[str] = []
    found = itineraries(payload)
    n = len(found)
    if n < MIN_ITINERARIES:
        reasons.append(f"only {n} itineraries (expected at least {MIN_ITINERARIES})")
    for pos, name in ((2, "top"), (3, "other")):
        if not isinstance(_at(payload, pos, 0), list):
            reasons.append(f"the {name} departing flights list (payload[{pos}][0]) is missing")
    prices = [it.price for it in found if it.price is not None]
    cents = []
    for p in prices:
        try:
            cents.append(normalize_price(p))
        except (TypeError, ValueError):
            reasons.append(f"unreadable price {p!r}")
    bad = [c for c in cents if not price_is_plausible(c)]
    if bad:
        reasons.append(f"{len(bad)} price(s) outside the plausible range, e.g. {bad[0]} cents")
    if found and len(prices) < n:
        reasons.append(f"{n - len(prices)} itineraries without a price")
    page_min = page_min_price(payload)
    if page_min is None:
        reasons.append("the page's own cheapest price (payload[25]) is missing")
    elif prices and min(prices) != page_min:
        reasons.append(f"the page says the cheapest is {page_min}, we read {min(prices)}")
    if found:
        complete = sum(
            1
            for it in found
            if it.airlines
            and it.segments
            and it.segments[0].depart_time
            and it.segments[-1].arrive_time
            and it.duration_min
        )
        if complete / n < MIN_COMPLETE_FRACTION:
            reasons.append(
                f"only {complete} of {n} itineraries have an airline, times and a duration"
            )
    return reasons, n


def run(
    conn: sqlite3.Connection,
    cfg: Config,
    transport: Transport | None = None,
    now: datetime | None = None,
) -> CanaryResult:
    """One canary request; records the verdict and the request (it spends the budget)."""
    now = now or clock_now()
    failures = FailureStore(cfg.failure_dir, keep=cfg.fetch.keep_failure_artifacts)
    src = GoogleHtmlSource(transport=transport, failures=failures)
    route = Route(cfg.collect.canary_dest, "canary", None, 1, 0, origin=cfg.home)
    depart = (now.date() + timedelta(days=DAYS_AHEAD)).isoformat()

    cur = conn.execute("INSERT INTO run_log (started_at, kind) VALUES (?, 'canary')", (utcnow(),))
    run_id = cur.lastrowid
    conn.commit()

    t0 = time.monotonic()
    outcome, error = "ok", None
    res: CanaryResult
    try:
        src.fetch(cfg, route, depart, None, 50)
    except NoResults as exc:
        outcome, error = "empty", str(exc)
    except SourceError as exc:
        outcome, error = exc.kind, str(exc)
        if isinstance(exc, BlockedError):
            until = fetch_mod.enter_cooldown(conn, cfg, now)
            error += f"; cooldown until {until}"
    latency = int((time.monotonic() - t0) * 1000)
    fetch_mod.log_attempt(conn, run_id, None, SOURCE_NAME, outcome, latency, error)

    page = src.last_page
    if outcome in ("blocked", "network") or page is None:
        res = CanaryResult("inconclusive", [f"{outcome}: {error}"])
    elif page.kind is not PageKind.RESULTS:
        res = CanaryResult("fail", [f"the page was not a results page ({page.kind.value})"])
    else:
        reasons, n = judge(page.payload)
        res = CanaryResult("fail" if reasons else "pass", reasons, n)

    if res.result == "fail" and src.last_response is not None:
        path = failures.save("canary", src.last_response, f"{cfg.home}-{route.dest}-{depart}")
        res.saved = str(path) if path else None
    if res.result == "fail":
        set_state(
            conn,
            STATE_LAYOUT_DRIFT,
            json.dumps({"at": iso(now), "reasons": res.reasons, "saved": res.saved}),
        )
        log.warning("[canary] FAILED: %s", res.summary())
    elif res.result == "pass":
        set_state(conn, STATE_LAYOUT_DRIFT, None)
        log.info("[canary] passed: %d itineraries", res.itineraries)
    else:
        log.warning("[canary] inconclusive: %s", "; ".join(res.reasons))
    set_state(
        conn,
        STATE_LAST_CANARY,
        json.dumps({"at": iso(now), "result": res.result, "detail": "; ".join(res.reasons)}),
    )
    conn.execute(
        "UPDATE run_log SET finished_at = ?, attempted = 1, succeeded = ?, failed = ?, "
        "blocked = ?, notes = ? WHERE id = ?",
        (
            utcnow(),
            1 if res.passed else 0,
            0 if res.passed else 1,
            1 if outcome == "blocked" else 0,
            f"canary {res.summary()}"[:500],
            run_id,
        ),
    )
    conn.commit()
    return res
