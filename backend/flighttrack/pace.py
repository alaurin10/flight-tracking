"""How many requests to send Google a day, learned from how Google answers.

Google publishes no limit for the Flights pages, and the one that applies to a home
address is unknown until it is hit. So the allowance is not a fixed number but a
pace the worker learns:

  * it starts at `collect.pace_start_per_day`;
  * after 24 clean hours (no block) in which it was mostly used (at least
    `BUSY_FRACTION` of it), it rises by `collect.pace_step_up_pct`, never above
    `collect.pace_max_per_day`. A quiet day proves nothing, so it does not raise;
  * a block cuts it to `BLOCK_KEEP` of the requests sent in the 24 hours before the
    block, holds it there for `HOLD_DAYS`, and then lets it rise by `RECOVER_PCT` a day,
    never past `BLOCK_CAP` of the lowest volume that drew a block in the last
    `BLOCK_MEMORY_DAYS`.

`collect.daily_request_budget` pins the allowance to a fixed number (nothing learned).

Every Google request counts, whatever lane it belongs to (`fetch_attempts.source`):
results pages and the fast-flights fallback are the detail lane, calendar calls the
calendar lane, Explore calls the explore lane, and the canary none. SerpApi
is not Google and counts nowhere. Each lane has a share of the allowance
(`collect.shares`); a lane with nothing to do, or paused, leaves its share to the others.

A lane is paused, without stopping the others, when Google answers its call with an
empty error instead of data (`gated`): 30 minutes, then 2, 6 and 24 hours.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import Config
from .store.db import get_state, iso, parse_ts, set_state
from .store.db import now as clock_now

STATE_PACE = "pace"
STATE_LANE_PAUSE = "lane_pause"

WINDOW = timedelta(hours=24)
MIN_PER_DAY = 24  # a block never cuts the pace below one request an hour
BUSY_FRACTION = 0.8
BLOCK_KEEP = 0.6
HOLD_DAYS = 3
RECOVER_PCT = 10.0
BLOCK_CAP = 0.85
BLOCK_MEMORY_DAYS = 30

# Requests to these sources do not go to Google, so they spend nothing.
NOT_GOOGLE = ("serpapi",)
LANES = ("detail", "calendar", "explore")
# fetch_attempts.source → lane; anything else is a detail request, except the canary.
LANE_OF_SOURCE = {"google_calendar": "calendar", "google_explore": "explore", "canary": None}
# Lanes the code can fill (explore since D2).
BUILT_LANES = ("detail", "calendar", "explore")
PAUSE_STEPS_HOURS = (0.5, 2.0, 6.0, 24.0)


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def _not_google_sql() -> tuple[str, tuple[str, ...]]:
    return f"source NOT IN ({','.join('?' * len(NOT_GOOGLE))})", NOT_GOOGLE


def requests_sent(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Google requests in the rolling 24 hours before `now`."""
    now = now or clock_now()
    cond, args = _not_google_sql()
    row = conn.execute(
        f"SELECT COUNT(*) FROM fetch_attempts WHERE at > ? AND {cond}", (iso(now - WINDOW), *args)
    ).fetchone()
    return int(row[0])


def lane_used(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, int]:
    """Requests per lane in the rolling 24 hours (the canary is in none)."""
    now = now or clock_now()
    cond, args = _not_google_sql()
    used = dict.fromkeys(LANES, 0)
    for row in conn.execute(
        f"SELECT source, COUNT(*) FROM fetch_attempts WHERE at > ? AND {cond} GROUP BY source",
        (iso(now - WINDOW), *args),
    ):
        lane = LANE_OF_SOURCE.get(row[0], "detail")
        if lane is not None:
            used[lane] += int(row[1])
    return used


def lane_shares(cfg: Config, lanes: tuple[str, ...] | None = None) -> dict[str, float]:
    """Each lane's fraction of the allowance, among `lanes` (default: the lanes that can
    run with this configuration). Fractions add up to 1."""
    if lanes is None:
        on = {"detail": True, "calendar": cfg.calendar.enabled, "explore": cfg.explore.enabled}
        lanes = tuple(lane for lane in BUILT_LANES if on[lane])
    raw = dict(zip(LANES, cfg.collect.shares, strict=True))
    total = sum(raw[lane] for lane in lanes) or 1
    return {lane: raw[lane] / total for lane in lanes}


# ---------------------------------------------------------------------------
# The pace
# ---------------------------------------------------------------------------


@dataclass
class Pace:
    per_day: int
    ceiling: int
    fixed: bool
    changed_at: str | None = None
    reason: str = "the starting pace"
    hold_until: str | None = None
    blocks: list[dict[str, Any]] = field(default_factory=list)  # {"at", "sent", "pace"}
    last_review_at: str | None = None
    last_review: str | None = None

    def state(self) -> dict[str, Any]:
        return {
            "per_day": self.per_day,
            "changed_at": self.changed_at,
            "reason": self.reason,
            "hold_until": self.hold_until,
            "blocks": self.blocks,
            "last_review_at": self.last_review_at,
            "last_review": self.last_review,
        }


def _raw(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        raw = json.loads(get_state(conn, STATE_PACE) or "null")
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def load(conn: sqlite3.Connection, cfg: Config) -> Pace:
    co = cfg.collect
    raw = _raw(conn)
    blocks = [b for b in raw.get("blocks") or [] if isinstance(b, dict) and "at" in b]
    if co.daily_request_budget is not None:
        n = co.daily_request_budget
        return Pace(n, n, True, reason="fixed by collect.daily_request_budget", blocks=blocks)
    try:
        per_day = int(raw.get("per_day") or co.pace_start_per_day)
    except (TypeError, ValueError):
        per_day = co.pace_start_per_day
    return Pace(
        per_day=max(1, min(per_day, co.pace_max_per_day)),
        ceiling=co.pace_max_per_day,
        fixed=False,
        changed_at=raw.get("changed_at"),
        reason=str(raw.get("reason") or "the starting pace"),
        hold_until=raw.get("hold_until"),
        blocks=blocks,
        last_review_at=raw.get("last_review_at"),
        last_review=raw.get("last_review"),
    )


def _save(conn: sqlite3.Connection, p: Pace) -> None:
    set_state(conn, STATE_PACE, json.dumps(p.state()))


def allowance(conn: sqlite3.Connection, cfg: Config) -> int:
    """Google requests allowed in any rolling 24 hours, right now."""
    return load(conn, cfg).per_day


def gap_seconds(conn: sqlite3.Connection, cfg: Config, requests: int = 0) -> float:
    """The even spacing for `requests` requests (default one slot of `batch_size`) at the
    current pace, before jitter."""
    n = requests or cfg.collect.batch_size
    return n * WINDOW.total_seconds() / max(1, allowance(conn, cfg))


def _recent_blocks(p: Pace, now: datetime) -> list[dict[str, Any]]:
    since = iso(now - timedelta(days=BLOCK_MEMORY_DAYS))
    return [b for b in p.blocks if str(b.get("at", "")) >= since]


def block_cap(p: Pace, now: datetime) -> int:
    """The highest the pace may go: the ceiling, and below the volumes blocked lately."""
    cap = p.ceiling
    for b in _recent_blocks(p, now):
        try:
            sent = int(b.get("sent") or 0)
        except (TypeError, ValueError):
            continue
        cap = min(cap, max(MIN_PER_DAY, math.floor(sent * BLOCK_CAP)))
    return cap


def _blocked_since(conn: sqlite3.Connection, since: datetime) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM fetch_attempts WHERE outcome = 'blocked' AND at > ? LIMIT 1",
            (iso(since),),
        ).fetchone()
        is not None
    )


def review(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> str:
    """Hourly: raise the pace when the last 24 hours earned it. Returns what it decided."""
    now = now or clock_now()
    p = load(conn, cfg)
    if p.fixed:
        return "fixed"

    def done(result: str, changed: bool = False) -> str:
        p.last_review_at, p.last_review = iso(now), result
        if changed:
            p.changed_at = iso(now)
        _save(conn, p)
        return result

    changed = parse_ts(p.changed_at)
    if changed is None:
        p.reason = "the starting pace"
        return done("started", changed=True)
    hold = parse_ts(p.hold_until)
    if hold is not None and now < hold:
        return done("holding after a block")
    if now - changed < WINDOW:
        return done("waiting for 24 hours at this pace")
    if _blocked_since(conn, now - WINDOW):
        return done("blocked in the last 24 hours")
    sent = requests_sent(conn, now)
    if sent < BUSY_FRACTION * p.per_day:
        return done(f"quiet: {sent} of {p.per_day} used")
    recovering = bool(_recent_blocks(p, now))
    step = RECOVER_PCT if recovering else cfg.collect.pace_step_up_pct
    target = min(block_cap(p, now), math.ceil(round(p.per_day * (1 + step / 100.0), 6)))
    if target <= p.per_day:
        return done("at its ceiling" if p.per_day >= p.ceiling else "at the cap below a block")
    old, p.per_day = p.per_day, target
    p.reason = f"raised from {old} after a clean day" + (" (recovering)" if recovering else "")
    p.hold_until = None
    return done("raised", changed=True)


def on_block(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> int:
    """Google blocked a request: cut the pace and remember the volume that drew it.
    Returns the new allowance."""
    now = now or clock_now()
    p = load(conn, cfg)
    sent = requests_sent(conn, now)
    p.blocks = [*_recent_blocks(p, now), {"at": iso(now), "sent": sent, "pace": p.per_day}]
    if not p.fixed:
        new = max(MIN_PER_DAY, math.floor(sent * BLOCK_KEEP))
        if new < p.per_day:
            p.reason = f"cut from {p.per_day} after a block at {sent} requests in 24 hours"
            p.per_day = new
        p.changed_at = iso(now)
        p.hold_until = iso(now + timedelta(days=HOLD_DAYS))
    _save(conn, p)
    return p.per_day


def next_review_at(p: Pace) -> str | None:
    """When the pace can next rise, at the earliest."""
    changed = parse_ts(p.changed_at)
    if p.fixed or changed is None:
        return None
    earliest = changed + WINDOW
    hold = parse_ts(p.hold_until)
    if hold is not None and hold > earliest:
        earliest = hold
    return iso(earliest)


# ---------------------------------------------------------------------------
# Lane pauses
# ---------------------------------------------------------------------------


def _pauses(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(get_state(conn, STATE_LANE_PAUSE) or "null")
    except ValueError:
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def lane_pause(conn: sqlite3.Connection, lane: str) -> dict[str, Any] | None:
    """The lane's pause record ({"until", "streak", "since", "reason"}), if it has one."""
    return _pauses(conn).get(lane)


def lane_paused(conn: sqlite3.Connection, lane: str, now: datetime | None = None) -> str | None:
    """When the lane may run again, if it is paused now."""
    now = now or clock_now()
    rec = lane_pause(conn, lane)
    until = parse_ts(rec.get("until")) if rec else None
    return iso(until) if until is not None and until > now else None


def pause_lane(
    conn: sqlite3.Connection, lane: str, reason: str, now: datetime | None = None
) -> str:
    """Google answered this lane's call without data: pause the lane, escalating."""
    now = now or clock_now()
    pauses = _pauses(conn)
    rec = pauses.get(lane) or {}
    streak = int(rec.get("streak") or 0) + 1
    hours = PAUSE_STEPS_HOURS[min(streak, len(PAUSE_STEPS_HOURS)) - 1]
    until = iso(now + timedelta(hours=hours))
    pauses[lane] = {
        "until": until,
        "streak": streak,
        "since": rec.get("since") or iso(now),
        "reason": reason,
    }
    set_state(conn, STATE_LANE_PAUSE, json.dumps(pauses))
    return until


def clear_lane(conn: sqlite3.Connection, lane: str) -> None:
    """The lane's call worked: forget its pause."""
    pauses = _pauses(conn)
    if pauses.pop(lane, None) is not None:
        set_state(conn, STATE_LANE_PAUSE, json.dumps(pauses) if pauses else None)
