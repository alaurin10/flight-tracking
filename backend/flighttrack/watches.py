"""What is tracked: trips, flexible searches, deal watches and deal feeds.

The database is the source of truth. Every change goes through this module,
which validates it, enforces the limits, and re-runs `expand` so the date grid
in `queries` is right the moment a trip or search is saved.

* A **trip** is one fixed date pair: "when should I book?"
* A **search** is a shape over many dates: some weekdays, a number of nights,
  one or more destinations, inside a window or a rolling horizon: "which
  weekend is cheapest?"
* `watch_queries` links each trip or search to the date pairs it wants. A date
  pair nobody wants is deactivated, never deleted: its price history stays.

Money is integer cents. Dates are ISO `YYYY-MM-DD`. Airport codes are checked
against `data/airports.csv`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import airports
from . import config as config_mod
from .config import DOW, Config
from .store.db import now as clock_now
from .store.db import utcnow

MAX_ACTIVE_TRIPS = 20
MAX_ACTIVE_SEARCHES = 10
MAX_ACTIVE_PAIRS = 400

TripStatus = Literal["active", "paused", "booked", "archived"]
SearchStatus = Literal["active", "paused", "archived"]
WatchStatus = Literal["active", "paused", "archived"]

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class WatchError(ValueError):
    """A change that cannot be saved, with a message a person can read."""


class LimitError(WatchError):
    """The change would exceed one of the limits on what is tracked."""


class NotFound(WatchError):
    pass


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def _airport(v: object) -> str:
    if not isinstance(v, str):
        raise ValueError("must be a three-letter airport code")
    code = v.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(f"{v!r} is not a three-letter airport code")
    if airports.get(code) is None:
        raise ValueError(f"{code} is not an airport with scheduled flights")
    return code


def _iso_date(v: object) -> str:
    if isinstance(v, date):
        v = v.isoformat()
    if not isinstance(v, str) or not _ISO.match(v):
        raise ValueError("must be a date YYYY-MM-DD")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{v} is not a real date") from None
    return v


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TripIn(_Base):
    name: str = Field(min_length=1, max_length=80)
    slug: str | None = None
    origin: str
    destination: str
    depart_date: str
    return_date: str | None = None
    target_price: int | None = Field(default=None, ge=0)
    max_stops: int | None = Field(default=None, ge=0, le=3)
    status: TripStatus = "active"
    notes: str | None = Field(default=None, max_length=2000)

    _codes = field_validator("origin", "destination", mode="before")(_airport)
    _dates = field_validator("depart_date", mode="before")(_iso_date)

    @field_validator("return_date", mode="before")
    @classmethod
    def _ret(cls, v: object) -> str | None:
        return None if v in (None, "") else _iso_date(v)

    @model_validator(mode="after")
    def _check(self) -> TripIn:
        if self.destination == self.origin:
            raise ValueError("the destination is the same as the origin")
        if self.return_date is not None and self.return_date <= self.depart_date:
            raise ValueError("the return date must be after the departure date")
        return self


class Trip(TripIn):
    id: int
    slug: str
    created_at: str
    updated_at: str


class SearchIn(_Base):
    name: str = Field(min_length=1, max_length=80)
    slug: str | None = None
    origin: str
    destinations: list[str] = Field(min_length=1, max_length=10)
    depart_dows: list[str] = Field(min_length=1, max_length=7)
    nights: int = Field(ge=0, le=60)
    window_start: str | None = None
    window_end: str | None = None
    min_days_ahead: int | None = Field(default=None, ge=0, le=365)
    target_price: int | None = Field(default=None, ge=0)
    max_stops: int | None = Field(default=None, ge=0, le=3)
    priority: int = Field(default=2, ge=1, le=3)
    status: SearchStatus = "active"

    _code = field_validator("origin", mode="before")(_airport)

    @field_validator("destinations", mode="before")
    @classmethod
    def _dests(cls, v: object) -> list[str]:
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list | tuple):
            raise ValueError("must be a list of airport codes")
        return list(dict.fromkeys(_airport(x) for x in v))

    @field_validator("depart_dows", mode="before")
    @classmethod
    def _dows(cls, v: object) -> list[str]:
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list | tuple):
            raise ValueError(f"must be a list of weekdays ({', '.join(DOW)})")
        out = [str(d).strip().upper()[:3] for d in v]
        bad = [d for d in out if d not in DOW]
        if bad:
            raise ValueError(f"{', '.join(bad)} is not a weekday; use {', '.join(DOW)}")
        return sorted(dict.fromkeys(out), key=lambda d: DOW[d])

    @field_validator("window_start", "window_end", mode="before")
    @classmethod
    def _window(cls, v: object) -> str | None:
        return None if v in (None, "") else _iso_date(v)

    @model_validator(mode="after")
    def _check(self) -> SearchIn:
        if self.origin in self.destinations:
            raise ValueError(f"{self.origin} is both the origin and a destination")
        if self.window_start and self.window_end and self.window_start > self.window_end:
            raise ValueError("the window starts after it ends")
        return self


class Search(SearchIn):
    id: int
    slug: str
    created_at: str
    updated_at: str


class DealWatchIn(_Base):
    name: str = Field(min_length=1, max_length=80)
    origins: list[str] = Field(default_factory=list, max_length=20)
    destinations: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    months: list[int] = Field(
        default_factory=list,
        max_length=12,
        description="travel months, 1-12; a post that names months must name one of these",
    )
    max_price: int | None = Field(default=None, ge=0)
    status: WatchStatus = "active"

    @field_validator("months", mode="before")
    @classmethod
    def _months(cls, v: object) -> list[int]:
        if v is None:
            return []
        if not isinstance(v, list | tuple) or not all(
            isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12 for m in v
        ):
            raise ValueError("must be a list of month numbers from 1 to 12")
        return sorted(set(v))

    @field_validator("origins", "destinations", "keywords", mode="before")
    @classmethod
    def _words(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list | tuple) or not all(isinstance(x, str) for x in v):
            raise ValueError("must be a list of words")
        return [x.strip() for x in v if x.strip()]


class DealWatch(DealWatchIn):
    id: int
    created_at: str
    updated_at: str

    def matcher(self) -> config_mod.DealWatch:
        """The shape `deals.matches` takes."""
        return config_mod.DealWatch(
            name=self.name,
            origins=tuple(self.origins),
            destinations=tuple(self.destinations),
            max_price=self.max_price,
            keywords=tuple(self.keywords),
            months=tuple(self.months),
        )


FeedKind = Literal["feed", "page"]


class DealFeedIn(_Base):
    url: str = Field(min_length=10, max_length=500)
    name: str | None = Field(default=None, max_length=80)
    status: WatchStatus = "active"
    kind: FeedKind = Field(
        default="feed",
        description="feed (RSS or Atom) or page (a web page read as a list of posts); "
        "found by fetching it once when it is saved",
    )

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not re.match(r"^https?://[^\s/]+\.[^\s]+$", v):
            raise ValueError("must be an http(s) address")
        return v


class DealFeed(DealFeedIn):
    id: int
    created_at: str


def _parse[M: BaseModel](model: type[M], data: BaseModel | dict[str, Any]) -> M:
    """Validate, turning pydantic's errors into one readable sentence."""
    raw = data.model_dump() if isinstance(data, BaseModel) else dict(data)
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            where = ".".join(str(x) for x in err["loc"])
            msg = str(err["msg"]).removeprefix("Value error, ")
            parts.append(f"{where}: {msg}" if where else msg)
        raise WatchError("; ".join(parts)) from None


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------


def _trip(row: sqlite3.Row) -> Trip:
    return Trip.model_validate(dict(row))


def _search(row: sqlite3.Row) -> Search:
    d = dict(row)
    d["destinations"] = json.loads(d["destinations"])
    d["depart_dows"] = json.loads(d["depart_dows"])
    return Search.model_validate(d)


def _deal_watch(row: sqlite3.Row) -> DealWatch:
    d = dict(row)
    for k in ("origins", "destinations", "keywords", "months"):
        d[k] = json.loads(d.get(k) or "[]")
    return DealWatch.model_validate(d)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return s or "watch"


def _slug_taken(conn: sqlite3.Connection, slug: str, *, table: str, own_id: int | None) -> bool:
    for t in ("trips", "searches"):
        row = conn.execute(f"SELECT id FROM {t} WHERE slug = ?", (slug,)).fetchone()
        if row is not None and not (t == table and row["id"] == own_id):
            return True
    return False


def _choose_slug(
    conn: sqlite3.Connection, wanted: str | None, name: str, *, table: str, own_id: int | None
) -> str:
    """Slugs are unique across trips and searches: they become `queries.pattern`."""
    if wanted:
        if not _SLUG.match(wanted):
            raise WatchError(
                "slug: use lower-case letters, digits, '-' and '_', starting with a letter or digit"
            )
        if _slug_taken(conn, wanted, table=table, own_id=own_id):
            raise WatchError(f"slug: {wanted!r} is already used by another trip or search")
        return wanted
    base = slugify(name)
    slug, n = base, 2
    while _slug_taken(conn, slug, table=table, own_id=own_id):
        slug, n = f"{base}-{n}", n + 1
    return slug


# ---------------------------------------------------------------------------
# limits and expansion
# ---------------------------------------------------------------------------


def _check_limits(conn: sqlite3.Connection, cfg: Config, today: date | None) -> None:
    from .expand import wanted_pairs

    trips = conn.execute("SELECT COUNT(*) FROM trips WHERE status = 'active'").fetchone()[0]
    if trips > MAX_ACTIVE_TRIPS:
        raise LimitError(
            f"That would make {trips} active trips; the limit is {MAX_ACTIVE_TRIPS}. "
            "Pause or archive one first."
        )
    searches = conn.execute("SELECT COUNT(*) FROM searches WHERE status = 'active'").fetchone()[0]
    if searches > MAX_ACTIVE_SEARCHES:
        raise LimitError(
            f"That would make {searches} active searches; the limit is {MAX_ACTIVE_SEARCHES}. "
            "Pause or archive one first."
        )
    pairs = len(wanted_pairs(conn, cfg, today))
    if pairs > MAX_ACTIVE_PAIRS:
        raise LimitError(
            f"That would track {pairs} date pairs in all; the limit is {MAX_ACTIVE_PAIRS}. "
            "Narrow a search (fewer weekdays, destinations or a shorter window) or pause one."
        )


def _save(
    conn: sqlite3.Connection,
    cfg: Config,
    write: Any,
    *,
    today: date | None,
    check: bool,
    expand: bool,
) -> Any:
    """Run `write(conn)` in a transaction, check the limits inside it, then expand."""
    try:
        conn.commit()
        conn.execute("BEGIN")
        result = write(conn)
        if check:
            _check_limits(conn, cfg, today)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if expand:
        refresh(conn, cfg, today=today)
    return result


def refresh(
    conn: sqlite3.Connection, cfg: Config, today: date | None = None, build_links: bool = True
) -> Any:
    """Re-derive routes, date pairs and links from the watches."""
    from .expand import expand

    return expand(conn, cfg, today=today, build_links=build_links)


# Lowest target among the trips and searches that want a date pair, else the
# route's own. Expects the outer query to alias `queries` as q and `routes` as r.
TARGET_SQL = """COALESCE(
    (SELECT MIN(t) FROM (
        SELECT tr.target_price AS t FROM watch_queries w JOIN trips tr ON tr.id = w.trip_id
         WHERE w.query_id = q.id AND tr.target_price IS NOT NULL
        UNION ALL
        SELECT s.target_price FROM watch_queries w JOIN searches s ON s.id = w.search_id
         WHERE w.query_id = q.id AND s.target_price IS NOT NULL)),
    r.target_price)"""


def target_for(conn: sqlite3.Connection, query_id: int) -> int | None:
    """The target price (cents) for one date pair: the lowest target among the trips
    and searches that want it, and the route's own target only if none has one."""
    row = conn.execute(
        f"SELECT {TARGET_SQL} AS target FROM queries q JOIN routes r ON r.id = q.route_id "
        "WHERE q.id = ?",
        (query_id,),
    ).fetchone()
    return None if row is None or row["target"] is None else int(row["target"])


# ---------------------------------------------------------------------------
# trips
# ---------------------------------------------------------------------------


def list_trips(conn: sqlite3.Connection, *, status: str | None = None) -> list[Trip]:
    sql, params = "SELECT * FROM trips", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    return [_trip(r) for r in conn.execute(sql + " ORDER BY depart_date, id", params)]


def get_trip(conn: sqlite3.Connection, key: int | str) -> Trip:
    col = "id" if isinstance(key, int) else "slug"
    row = conn.execute(f"SELECT * FROM trips WHERE {col} = ?", (key,)).fetchone()
    if row is None:
        raise NotFound(f"no trip {key!r}")
    return _trip(row)


def create_trip(
    conn: sqlite3.Connection,
    cfg: Config,
    data: TripIn | dict[str, Any],
    *,
    today: date | None = None,
    expand: bool = True,
) -> Trip:
    t = _parse(TripIn, data)

    def write(c: sqlite3.Connection) -> int:
        slug = _choose_slug(c, t.slug, t.name, table="trips", own_id=None)
        now = utcnow()
        cur = c.execute(
            """INSERT INTO trips (slug, name, origin, destination, depart_date, return_date,
                   target_price, max_stops, status, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                slug,
                t.name,
                t.origin,
                t.destination,
                t.depart_date,
                t.return_date,
                t.target_price,
                t.max_stops,
                t.status,
                t.notes,
                now,
                now,
            ),
        )
        return int(cur.lastrowid or 0)

    trip_id = _save(conn, cfg, write, today=today, check=t.status == "active", expand=expand)
    return get_trip(conn, trip_id)


def update_trip(
    conn: sqlite3.Connection,
    cfg: Config,
    trip_id: int,
    data: TripIn | dict[str, Any],
    *,
    today: date | None = None,
    expand: bool = True,
) -> Trip:
    old = get_trip(conn, trip_id)
    t = _parse(TripIn, data)

    def write(c: sqlite3.Connection) -> None:
        slug = _choose_slug(c, t.slug or old.slug, t.name, table="trips", own_id=trip_id)
        c.execute(
            """UPDATE trips SET slug = ?, name = ?, origin = ?, destination = ?, depart_date = ?,
                   return_date = ?, target_price = ?, max_stops = ?, status = ?, notes = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                slug,
                t.name,
                t.origin,
                t.destination,
                t.depart_date,
                t.return_date,
                t.target_price,
                t.max_stops,
                t.status,
                t.notes,
                utcnow(),
                trip_id,
            ),
        )

    _save(conn, cfg, write, today=today, check=t.status == "active", expand=expand)
    return get_trip(conn, trip_id)


def set_trip_status(
    conn: sqlite3.Connection,
    cfg: Config,
    trip_id: int,
    status: TripStatus,
    *,
    today: date | None = None,
) -> Trip:
    old = get_trip(conn, trip_id)
    return update_trip(conn, cfg, trip_id, {**_fields(old, TripIn), "status": status}, today=today)


def delete_trip(
    conn: sqlite3.Connection, cfg: Config, trip_id: int, *, today: date | None = None
) -> None:
    """Delete the trip. Its date pairs stay, with their history; unwanted ones go inactive."""
    get_trip(conn, trip_id)

    def write(c: sqlite3.Connection) -> None:
        c.execute("DELETE FROM watch_queries WHERE trip_id = ?", (trip_id,))
        c.execute("DELETE FROM trips WHERE id = ?", (trip_id,))

    _save(conn, cfg, write, today=today, check=False, expand=True)


# ---------------------------------------------------------------------------
# searches
# ---------------------------------------------------------------------------


def list_searches(conn: sqlite3.Connection, *, status: str | None = None) -> list[Search]:
    sql, params = "SELECT * FROM searches", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    return [_search(r) for r in conn.execute(sql + " ORDER BY priority, id", params)]


def get_search(conn: sqlite3.Connection, key: int | str) -> Search:
    col = "id" if isinstance(key, int) else "slug"
    row = conn.execute(f"SELECT * FROM searches WHERE {col} = ?", (key,)).fetchone()
    if row is None:
        raise NotFound(f"no search {key!r}")
    return _search(row)


def _search_values(s: SearchIn) -> tuple[Any, ...]:
    return (
        s.name,
        s.origin,
        json.dumps(s.destinations),
        json.dumps(s.depart_dows),
        s.nights,
        s.window_start,
        s.window_end,
        s.min_days_ahead,
        s.target_price,
        s.max_stops,
        s.priority,
        s.status,
    )


def create_search(
    conn: sqlite3.Connection,
    cfg: Config,
    data: SearchIn | dict[str, Any],
    *,
    today: date | None = None,
    expand: bool = True,
) -> Search:
    s = _parse(SearchIn, data)

    def write(c: sqlite3.Connection) -> int:
        slug = _choose_slug(c, s.slug, s.name, table="searches", own_id=None)
        now = utcnow()
        cur = c.execute(
            """INSERT INTO searches (slug, name, origin, destinations, depart_dows, nights,
                   window_start, window_end, min_days_ahead, target_price, max_stops, priority,
                   status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slug, *_search_values(s), now, now),
        )
        return int(cur.lastrowid or 0)

    search_id = _save(conn, cfg, write, today=today, check=s.status == "active", expand=expand)
    return get_search(conn, search_id)


def update_search(
    conn: sqlite3.Connection,
    cfg: Config,
    search_id: int,
    data: SearchIn | dict[str, Any],
    *,
    today: date | None = None,
    expand: bool = True,
) -> Search:
    old = get_search(conn, search_id)
    s = _parse(SearchIn, data)

    def write(c: sqlite3.Connection) -> None:
        slug = _choose_slug(c, s.slug or old.slug, s.name, table="searches", own_id=search_id)
        c.execute(
            """UPDATE searches SET name = ?, origin = ?, destinations = ?, depart_dows = ?,
                   nights = ?, window_start = ?, window_end = ?, min_days_ahead = ?,
                   target_price = ?, max_stops = ?, priority = ?, status = ?, slug = ?,
                   updated_at = ?
               WHERE id = ?""",
            (*_search_values(s), slug, utcnow(), search_id),
        )

    _save(conn, cfg, write, today=today, check=s.status == "active", expand=expand)
    return get_search(conn, search_id)


def set_search_status(
    conn: sqlite3.Connection,
    cfg: Config,
    search_id: int,
    status: SearchStatus,
    *,
    today: date | None = None,
) -> Search:
    old = get_search(conn, search_id)
    return update_search(
        conn, cfg, search_id, {**_fields(old, SearchIn), "status": status}, today=today
    )


def delete_search(
    conn: sqlite3.Connection, cfg: Config, search_id: int, *, today: date | None = None
) -> None:
    get_search(conn, search_id)

    def write(c: sqlite3.Connection) -> None:
        c.execute("DELETE FROM watch_queries WHERE search_id = ?", (search_id,))
        c.execute("DELETE FROM searches WHERE id = ?", (search_id,))

    _save(conn, cfg, write, today=today, check=False, expand=True)


@dataclass(frozen=True)
class SearchPreview:
    pairs: int  # date pairs this search would track
    total_pairs: int  # date pairs tracked in all, with this search saved as active


def preview_search(
    conn: sqlite3.Connection,
    cfg: Config,
    data: SearchIn | dict[str, Any],
    *,
    search_id: int | None = None,
    today: date | None = None,
) -> SearchPreview:
    """How many date pairs a search would track if saved as active, and the total with
    it, without saving anything: the change is made inside a transaction that is always
    rolled back. `search_id` previews a change to an existing search."""
    from .expand import wanted_owners

    s = _parse(SearchIn, data).model_copy(update={"status": "active"})
    if search_id is not None:
        get_search(conn, search_id)
    try:
        conn.commit()
        conn.execute("BEGIN")
        if search_id is None:
            now = utcnow()
            cur = conn.execute(
                """INSERT INTO searches (slug, name, origin, destinations, depart_dows, nights,
                       window_start, window_end, min_days_ahead, target_price, max_stops,
                       priority, status, created_at, updated_at)
                   VALUES ('__preview__', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*_search_values(s), now, now),
            )
            sid = int(cur.lastrowid or 0)
        else:
            sid = search_id
            conn.execute(
                """UPDATE searches SET name = ?, origin = ?, destinations = ?, depart_dows = ?,
                       nights = ?, window_start = ?, window_end = ?, min_days_ahead = ?,
                       target_price = ?, max_stops = ?, priority = ?, status = ?
                   WHERE id = ?""",
                (*_search_values(s), sid),
            )
        owners = wanted_owners(conn, cfg, today)
    finally:
        conn.execute("ROLLBACK")
    mine = sum(1 for who in owners.values() if any(o[0] == "search" and o[1] == sid for o in who))
    return SearchPreview(pairs=mine, total_pairs=len(owners))


# ---------------------------------------------------------------------------
# deal watches and feeds (no date pairs, so no expansion)
# ---------------------------------------------------------------------------


def list_deal_watches(conn: sqlite3.Connection, *, status: str | None = None) -> list[DealWatch]:
    sql, params = "SELECT * FROM deal_watches", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    return [_deal_watch(r) for r in conn.execute(sql + " ORDER BY id", params)]


def get_deal_watch(conn: sqlite3.Connection, watch_id: int) -> DealWatch:
    row = conn.execute("SELECT * FROM deal_watches WHERE id = ?", (watch_id,)).fetchone()
    if row is None:
        raise NotFound(f"no deal watch {watch_id}")
    return _deal_watch(row)


def _watch_values(w: DealWatchIn) -> tuple[Any, ...]:
    return (
        w.name,
        json.dumps(w.origins),
        json.dumps(w.destinations),
        json.dumps(w.keywords),
        json.dumps(w.months),
        w.max_price,
        w.status,
    )


def create_deal_watch(conn: sqlite3.Connection, data: DealWatchIn | dict[str, Any]) -> DealWatch:
    w = _parse(DealWatchIn, data)
    now = utcnow()
    with conn:
        cur = conn.execute(
            """INSERT INTO deal_watches (name, origins, destinations, keywords, months,
                   max_price, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*_watch_values(w), now, now),
        )
    return get_deal_watch(conn, int(cur.lastrowid or 0))


def update_deal_watch(
    conn: sqlite3.Connection, watch_id: int, data: DealWatchIn | dict[str, Any]
) -> DealWatch:
    get_deal_watch(conn, watch_id)
    w = _parse(DealWatchIn, data)
    with conn:
        conn.execute(
            """UPDATE deal_watches SET name = ?, origins = ?, destinations = ?, keywords = ?,
                   months = ?, max_price = ?, status = ?, updated_at = ? WHERE id = ?""",
            (*_watch_values(w), utcnow(), watch_id),
        )
    return get_deal_watch(conn, watch_id)


def set_deal_watch_status(
    conn: sqlite3.Connection, watch_id: int, status: WatchStatus
) -> DealWatch:
    old = get_deal_watch(conn, watch_id)
    return update_deal_watch(conn, watch_id, {**_fields(old, DealWatchIn), "status": status})


def delete_deal_watch(conn: sqlite3.Connection, watch_id: int) -> None:
    get_deal_watch(conn, watch_id)
    with conn:
        conn.execute("DELETE FROM deal_watches WHERE id = ?", (watch_id,))


def active_deal_watches(conn: sqlite3.Connection) -> list[config_mod.DealWatch]:
    return [w.matcher() for w in list_deal_watches(conn, status="active")]


def months_between(start: str, end: str) -> tuple[int, ...]:
    """The calendar months (1-12) from one ISO date to another, inclusive."""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if b < a:
        a, b = b, a
    out: list[int] = []
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month) and len(out) < 12:
        out.append(m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return tuple(sorted(set(out)))


def _place_words(conn: sqlite3.Connection, origin: str, dest: str) -> tuple[str, ...]:
    """How a deal post may name a destination: the route's label, the airport's city
    and its code ("Tokyo", "Narita", "NRT")."""
    row = conn.execute(
        "SELECT label FROM routes WHERE origin = ? AND destination = ?", (origin, dest)
    ).fetchone()
    words = [row["label"] if row and row["label"] else "", airports.city(dest), dest]
    return tuple(dict.fromkeys(w for w in words if w))


def trip_and_search_watches(conn: sqlite3.Connection) -> list[config_mod.DealWatch]:
    """Active trips and flexible searches as deal watches, so a deal page's post for the
    same trip alerts too: from the origin (city or code) to one of the destinations, at or
    under the target when there is one, in the trip's months or the search's window."""
    out: list[config_mod.DealWatch] = []
    today = clock_now().date().isoformat()
    for t in list_trips(conn, status="active"):
        if (t.return_date or t.depart_date) < today:
            continue  # already flown
        out.append(
            config_mod.DealWatch(
                name=t.name,
                origins=(airports.city(t.origin), t.origin),
                destinations=_place_words(conn, t.origin, t.destination),
                max_price=t.target_price,
                months=months_between(t.depart_date, t.return_date or t.depart_date),
            )
        )
    for s in list_searches(conn, status="active"):
        dests: list[str] = []
        for d in s.destinations:
            dests.extend(_place_words(conn, s.origin, d))
        months: tuple[int, ...] = ()
        if s.window_start and s.window_end:
            months = months_between(s.window_start, s.window_end)
        out.append(
            config_mod.DealWatch(
                name=s.name,
                origins=(airports.city(s.origin), s.origin),
                destinations=tuple(dict.fromkeys(dests)),
                max_price=s.target_price,
                months=months,
            )
        )
    return out


def deal_matchers(conn: sqlite3.Connection) -> list[config_mod.DealWatch]:
    """Everything a deal post is matched against: the deal watches (regions and words)
    first, then the trips and flexible searches."""
    return [*active_deal_watches(conn), *trip_and_search_watches(conn)]


def list_deal_feeds(conn: sqlite3.Connection, *, status: str | None = None) -> list[DealFeed]:
    sql, params = "SELECT * FROM deal_feeds", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    return [DealFeed.model_validate(dict(r)) for r in conn.execute(sql + " ORDER BY id", params)]


def get_deal_feed(conn: sqlite3.Connection, feed_id: int) -> DealFeed:
    row = conn.execute("SELECT * FROM deal_feeds WHERE id = ?", (feed_id,)).fetchone()
    if row is None:
        raise NotFound(f"no deal feed {feed_id}")
    return DealFeed.model_validate(dict(row))


# Fetches an address once and says what to read there: (address, kind). A web page that
# advertises a feed gives the feed's address. Raises WatchError when there is nothing.
FeedCheck = Callable[[str], tuple[str, FeedKind]]


def _taken(conn: sqlite3.Connection, url: str, own_id: int | None) -> None:
    row = conn.execute("SELECT id FROM deal_feeds WHERE url = ?", (url,)).fetchone()
    if row is not None and row["id"] != own_id:
        raise WatchError(f"url: {url} is already a deal source")


def create_deal_feed(
    conn: sqlite3.Connection,
    data: DealFeedIn | dict[str, Any],
    *,
    check: FeedCheck | None = None,
) -> DealFeed:
    """`check(url)` (the API fetches it once) decides the address and kind, or refuses;
    it runs after the cheap checks and before anything is written."""
    f = _parse(DealFeedIn, data)
    _taken(conn, f.url, None)
    url, kind = f.url, f.kind
    if check is not None:
        url, kind = check(f.url)
        if url != f.url:
            _taken(conn, url, None)
    with conn:
        cur = conn.execute(
            "INSERT INTO deal_feeds (url, name, status, kind, created_at) VALUES (?, ?, ?, ?, ?)",
            (url, f.name, f.status, kind, utcnow()),
        )
    return get_deal_feed(conn, int(cur.lastrowid or 0))


def update_deal_feed(
    conn: sqlite3.Connection,
    feed_id: int,
    data: DealFeedIn | dict[str, Any],
    *,
    check: FeedCheck | None = None,
) -> DealFeed:
    """`check` runs only when the URL changes; otherwise the stored kind is kept."""
    old = get_deal_feed(conn, feed_id)
    f = _parse(DealFeedIn, data)
    _taken(conn, f.url, feed_id)
    url, kind = f.url, old.kind
    if f.url != old.url:
        kind = f.kind
        if check is not None:
            url, kind = check(f.url)
            if url != f.url:
                _taken(conn, url, feed_id)
    with conn:
        conn.execute(
            "UPDATE deal_feeds SET url = ?, name = ?, status = ?, kind = ? WHERE id = ?",
            (url, f.name, f.status, kind, feed_id),
        )
    return get_deal_feed(conn, feed_id)


def set_deal_feed_status(conn: sqlite3.Connection, feed_id: int, status: WatchStatus) -> DealFeed:
    old = get_deal_feed(conn, feed_id)
    return update_deal_feed(conn, feed_id, {**_fields(old, DealFeedIn), "status": status})


def delete_deal_feed(conn: sqlite3.Connection, feed_id: int) -> None:
    get_deal_feed(conn, feed_id)
    with conn:
        conn.execute("DELETE FROM deal_feeds WHERE id = ?", (feed_id,))


def active_sources(conn: sqlite3.Connection, cfg: Config) -> list[tuple[str, FeedKind]]:
    """What to poll, with how to read it: the active deal sources, or, while none has
    been added, the default feeds in config/flighttrack.yaml."""
    if conn.execute("SELECT COUNT(*) FROM deal_feeds").fetchone()[0] == 0:
        return [(u, "feed") for u in cfg.deals.feeds]
    return [(f.url, f.kind) for f in list_deal_feeds(conn, status="active")]


def active_feed_urls(conn: sqlite3.Connection, cfg: Config) -> list[str]:
    """The feeds to poll: the active rows of `deal_feeds`, or, while that table is
    still empty, the default feeds in config/flighttrack.yaml."""
    if conn.execute("SELECT COUNT(*) FROM deal_feeds").fetchone()[0] == 0:
        return list(cfg.deals.feeds)
    return [f.url for f in list_deal_feeds(conn, status="active")]


def _fields(obj: BaseModel, model: type[BaseModel]) -> dict[str, Any]:
    """The fields of `model` taken from a stored row, for a read-modify-write."""
    return {k: getattr(obj, k) for k in model.model_fields}


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    def n(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    return {
        "trips": n("SELECT COUNT(*) FROM trips WHERE status = 'active'"),
        "searches": n("SELECT COUNT(*) FROM searches WHERE status = 'active'"),
        "deal_watches": n("SELECT COUNT(*) FROM deal_watches WHERE status = 'active'"),
        "pairs": n(
            "SELECT COUNT(*) FROM queries q JOIN routes r ON r.id = q.route_id "
            "WHERE q.active = 1 AND r.active = 1"
        ),
    }


# ---------------------------------------------------------------------------
# import-config / export-config: the old single-file shape
# ---------------------------------------------------------------------------


_KINDS = ("routes", "trips", "searches", "deal_watches", "deal_feeds")


@dataclass
class ImportSummary:
    created: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_KINDS, 0))
    updated: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_KINDS, 0))
    unchanged: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_KINDS, 0))
    expansion: Any = None

    @property
    def changed(self) -> bool:
        return any(self.created.values()) or any(self.updated.values())

    def lines(self) -> list[str]:
        out = []
        for k in _KINDS:
            out.append(
                f"{k.replace('_', ' ')}: {self.created[k]} created, {self.updated[k]} updated, "
                f"{self.unchanged[k]} unchanged"
            )
        if self.expansion is not None:
            out.append(f"date grid: {self.expansion.summary()}")
        return out


def humanize(slug: str) -> str:
    text = slug.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else slug


def _upsert_route(
    conn: sqlite3.Connection, summary: ImportSummary, origin: str, r: config_mod.Route
) -> None:
    """A route's own label and target, which date pairs fall back on."""
    row = conn.execute(
        "SELECT label, target_price FROM routes WHERE origin = ? AND destination = ?",
        (origin, r.dest),
    ).fetchone()
    if row is None:
        with conn:
            conn.execute(
                "INSERT INTO routes (origin, destination, label, target_price, priority, "
                "max_stops, active) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (origin, r.dest, r.label, r.target_price, r.priority, r.max_stops),
            )
        summary.created["routes"] += 1
    elif (row["label"], row["target_price"]) != (r.label, r.target_price):
        with conn:
            conn.execute(
                "UPDATE routes SET label = ?, target_price = ? "
                "WHERE origin = ? AND destination = ?",
                (r.label, r.target_price, origin, r.dest),
            )
        summary.updated["routes"] += 1
    else:
        summary.unchanged["routes"] += 1


def _common(values: list[Any]) -> Any:
    """The one value every item shares, else None."""
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


def import_watch_file(
    conn: sqlite3.Connection,
    cfg: Config,
    wf: config_mod.WatchFile,
    *,
    today: date | None = None,
    build_links: bool = True,
) -> ImportSummary:
    """Create what is missing and update what exists, matched by slug (trips, searches),
    name (deal watches) or URL (feeds). Never deletes anything.

    A pattern becomes one search with all its destinations. A route's target and
    priority carry onto the searches that use it: the target when every destination
    shares it (otherwise each route keeps its own target, which its date pairs fall
    back on), and the highest priority among them.
    """
    summary = ImportSummary()
    home = wf.home
    by_dest = {r.dest: r for r in wf.routes}
    for r in wf.routes:
        _upsert_route(conn, summary, r.origin or home, r)

    for t in wf.trips:
        route = by_dest.get(t.dest)
        existing = conn.execute("SELECT * FROM trips WHERE slug = ?", (t.name,)).fetchone()
        data = {
            "name": t.title or humanize(t.name),
            "slug": t.name,
            "origin": t.origin or home,
            "destination": t.dest,
            "depart_date": t.depart,
            "return_date": t.ret,
            "target_price": t.target_price,
            "max_stops": t.max_stops if t.max_stops is not None else (route and route.max_stops),
            "status": t.status or (existing["status"] if existing else "active"),
            "notes": t.notes if t.notes is not None else (existing["notes"] if existing else None),
        }
        if existing is None:
            create_trip(conn, cfg, data, today=today, expand=False)
            summary.created["trips"] += 1
        elif _parse(TripIn, data).model_dump() != _fields(_trip(existing), TripIn):
            update_trip(conn, cfg, existing["id"], data, today=today, expand=False)
            summary.updated["trips"] += 1
        else:
            summary.unchanged["trips"] += 1

    for pe in wf.patterns:
        p = pe.pattern
        dests = list(p.routes) or [r.dest for r in wf.routes]
        rs = [by_dest[d] for d in dests if d in by_dest]
        existing = conn.execute("SELECT * FROM searches WHERE slug = ?", (p.name,)).fetchone()
        data = {
            "name": pe.title or humanize(p.name),
            "slug": p.name,
            "origin": pe.origin or home,
            "destinations": dests,
            "depart_dows": list(p.depart_dows),
            "nights": p.nights,
            "window_start": p.window_start,
            "window_end": p.window_end,
            "min_days_ahead": p.min_days_ahead,
            "target_price": pe.target_price
            if pe.target_price is not None
            else _common([r.target_price for r in rs]),
            "max_stops": pe.max_stops
            if pe.max_stops is not None
            else _common([r.max_stops for r in rs]),
            "priority": pe.priority or min((r.priority for r in rs), default=2),
            "status": pe.status or (existing["status"] if existing else "active"),
        }
        if existing is None:
            create_search(conn, cfg, data, today=today, expand=False)
            summary.created["searches"] += 1
        elif _parse(SearchIn, data).model_dump() != _fields(_search(existing), SearchIn):
            update_search(conn, cfg, existing["id"], data, today=today, expand=False)
            summary.updated["searches"] += 1
        else:
            summary.unchanged["searches"] += 1

    for w in wf.deal_watches:
        row = conn.execute("SELECT * FROM deal_watches WHERE name = ?", (w.name,)).fetchone()
        wdata = {
            "name": w.name,
            "origins": list(w.origins),
            "destinations": list(w.destinations),
            "keywords": list(w.keywords),
            "months": list(w.months),
            "max_price": w.max_price,
            "status": row["status"] if row else "active",
        }
        if row is None:
            create_deal_watch(conn, wdata)
            summary.created["deal_watches"] += 1
        elif _parse(DealWatchIn, wdata).model_dump() != _fields(_deal_watch(row), DealWatchIn):
            update_deal_watch(conn, row["id"], wdata)
            summary.updated["deal_watches"] += 1
        else:
            summary.unchanged["deal_watches"] += 1

    sources = [(u, "feed") for u in wf.deal_feeds] + [(u, "page") for u in wf.deal_pages]
    for url, kind in sources:
        if conn.execute("SELECT 1 FROM deal_feeds WHERE url = ?", (url,)).fetchone():
            summary.unchanged["deal_feeds"] += 1
        else:
            create_deal_feed(conn, {"url": url, "kind": kind})
            summary.created["deal_feeds"] += 1

    summary.expansion = refresh(conn, cfg, today=today, build_links=build_links)
    return summary


def export_watch_file(conn: sqlite3.Connection, cfg: Config) -> dict[str, Any]:
    """Everything tracked, in the shape `import-config` reads (for backups)."""
    home = cfg.home
    trips = list_trips(conn)
    searches = list_searches(conn)
    dests: dict[str, str] = {}
    for t in trips:
        dests.setdefault(t.destination, t.origin)
    for s in searches:
        for d in s.destinations:
            dests.setdefault(d, s.origin)
    routes = []
    for dest, origin in sorted(dests.items()):
        row = conn.execute(
            "SELECT label, target_price, priority, max_stops FROM routes "
            "WHERE origin = ? AND destination = ?",
            (origin, dest),
        ).fetchone()
        entry: dict[str, Any] = {
            "dest": dest,
            "label": row["label"] if row else airports.city(dest),
        }
        if row and row["target_price"] is not None:
            entry["target_price"] = row["target_price"]
        if row:
            entry["priority"] = row["priority"]
        routes.append(entry)

    def trip_entry(t: Trip) -> dict[str, Any]:
        e: dict[str, Any] = {"name": t.slug, "title": t.name, "dest": t.destination}
        if t.origin != home:
            e["origin"] = t.origin
        e["depart"] = t.depart_date
        if t.return_date:
            e["return"] = t.return_date
        for k in ("target_price", "max_stops", "notes"):
            if getattr(t, k) is not None:
                e[k] = getattr(t, k)
        if t.status != "active":
            e["status"] = t.status
        return e

    def pattern_entry(s: Search) -> dict[str, Any]:
        e: dict[str, Any] = {"name": s.slug, "title": s.name}
        if s.origin != home:
            e["origin"] = s.origin
        e["depart_dow"] = list(s.depart_dows)
        e["nights"] = s.nights
        e["routes"] = list(s.destinations)
        if s.window_start or s.window_end:
            e["window"] = {k: v for k, v in (("start", s.window_start), ("end", s.window_end)) if v}
        for k in ("min_days_ahead", "target_price", "max_stops"):
            if getattr(s, k) is not None:
                e[k] = getattr(s, k)
        e["priority"] = s.priority
        if s.status != "active":
            e["status"] = s.status
        return e

    watches = []
    for w in list_deal_watches(conn):
        we: dict[str, Any] = {
            "name": w.name,
            "origins": w.origins,
            "destinations": w.destinations,
        }
        if w.keywords:
            we["keywords"] = w.keywords
        if w.max_price is not None:
            we["max_price"] = w.max_price
        if w.months:
            we["months"] = w.months
        watches.append(we)
    return {
        "home": home,
        "routes": routes,
        "trips": [trip_entry(t) for t in trips],
        "patterns": [pattern_entry(s) for s in searches],
        "deals": {
            "watches": watches,
            "feeds": [f.url for f in list_deal_feeds(conn) if f.kind == "feed"],
            **(
                {"pages": pages}
                if (pages := [f.url for f in list_deal_feeds(conn) if f.kind == "page"])
                else {}
            ),
        },
    }
