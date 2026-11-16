"""Owner-only endpoints (see api/auth.py for the owner role).

Every change goes through `watches.py`, which validates it, enforces the limits
and re-expands the date grid. Creating or changing an active trip or search
queues a fetch job, so the worker prices it within a minute or so. A change
that cannot be saved answers 422 with a sentence a person can read.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import app_settings, deals, jobs, pace, watches
from .. import fetch as fetch_mod
from ..config import Config
from ..gflights.transport import Transport, default_transport
from ..settings import Settings
from ..store.db import now as clock_now
from ..store.db import open_db
from .auth import Owner
from .models import (
    BudgetOut,
    CollectingOut,
    DealTestIn,
    DealTestOut,
    JobIn,
    JobOut,
    OkOut,
    SearchPreviewOut,
    SearchSaved,
    StatusIn,
    TripSaved,
)


def db_rw(request: Request) -> Iterator[sqlite3.Connection]:
    settings: Settings = request.app.state.settings
    conn = open_db(settings.database)
    try:
        yield conn
    finally:
        conn.close()


RW = Annotated[sqlite3.Connection, Depends(db_rw)]


def _cfg(request: Request) -> Config:
    cfg: Config = request.app.state.cfg
    return cfg


def _today() -> date:
    return clock_now().date()


def _job(conn: sqlite3.Connection, job_id: int) -> JobOut:
    job = jobs.get(conn, job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    return JobOut.model_validate(job)


def _queue_trip(conn: sqlite3.Connection, trip: watches.Trip) -> int | None:
    if trip.status != "active":
        return None
    return jobs.enqueue(conn, "fetch_trip", {"trip": trip.slug}, "owner")


def _queue_search(conn: sqlite3.Connection, search: watches.Search) -> int | None:
    if search.status != "active":
        return None
    return jobs.enqueue(conn, "fetch_search", {"search": search.slug}, "owner")


def _cooldown_check(conn: sqlite3.Connection) -> None:
    """A fetch asked for during a cooldown would only fail: say when collecting resumes."""
    until = fetch_mod.cooldown_active(conn, clock_now())
    if until:
        raise HTTPException(
            409,
            f"Collecting is paused until {until} (Google asked us to slow down); "
            "nothing was queued.",
        )


def _feed_check(request: Request) -> watches.FeedCheck | None:
    """Fetch a new deal source's address once: a feed, a page advertising a feed (its feed
    is kept), or a page listing posts; anything else is refused. Skipped with
    FLIGHTTRACK_SOURCE=fake, which makes no outside requests (the kind sent is kept)."""
    settings: Settings = request.app.state.settings
    if settings.fake_source:
        return None
    transport: Transport | None = getattr(request.app.state, "feed_transport", None)

    def check(url: str) -> tuple[str, watches.FeedKind]:
        try:
            found = deals.inspect_source(url, transport or default_transport("urllib"))
        except deals.FeedError as exc:
            raise watches.WatchError(f"url: {url} can't be added: {exc}") from None
        return found.url, "page" if found.kind == "page" else "feed"

    return check


def _status(body: StatusIn, allowed: tuple[str, ...]) -> Any:
    if body.status not in allowed:
        raise watches.WatchError(f"status: use one of {', '.join(allowed)}")
    return body.status


def make_admin_router() -> APIRouter:
    r = APIRouter(prefix="/api/admin")

    # -- trips ----------------------------------------------------------------

    @r.get("/trips", response_model=list[watches.Trip])
    def list_trips(_: Owner, conn: RW) -> list[watches.Trip]:
        return watches.list_trips(conn)

    @r.post("/trips", response_model=TripSaved, status_code=201)
    def create_trip(body: watches.TripIn, request: Request, _: Owner, conn: RW) -> TripSaved:
        trip = watches.create_trip(conn, _cfg(request), body, today=_today())
        return TripSaved(trip=trip, job_id=_queue_trip(conn, trip))

    @r.put("/trips/{trip_id}", response_model=TripSaved)
    def update_trip(
        trip_id: int, body: watches.TripIn, request: Request, _: Owner, conn: RW
    ) -> TripSaved:
        trip = watches.update_trip(conn, _cfg(request), trip_id, body, today=_today())
        return TripSaved(trip=trip, job_id=_queue_trip(conn, trip))

    @r.put("/trips/{trip_id}/status", response_model=TripSaved)
    def trip_status(
        trip_id: int, body: StatusIn, request: Request, _: Owner, conn: RW
    ) -> TripSaved:
        status = _status(body, ("active", "paused", "booked", "archived"))
        trip = watches.set_trip_status(conn, _cfg(request), trip_id, status, today=_today())
        return TripSaved(trip=trip, job_id=_queue_trip(conn, trip))

    @r.delete("/trips/{trip_id}", response_model=OkOut)
    def delete_trip(trip_id: int, request: Request, _: Owner, conn: RW) -> OkOut:
        """Deletes the trip; its price history stays with the date pair."""
        watches.delete_trip(conn, _cfg(request), trip_id, today=_today())
        return OkOut(ok=True)

    # -- searches -------------------------------------------------------------

    @r.get("/searches", response_model=list[watches.Search])
    def list_searches(_: Owner, conn: RW) -> list[watches.Search]:
        return watches.list_searches(conn)

    @r.post("/searches", response_model=SearchSaved, status_code=201)
    def create_search(body: watches.SearchIn, request: Request, _: Owner, conn: RW) -> SearchSaved:
        search = watches.create_search(conn, _cfg(request), body, today=_today())
        return SearchSaved(search=search, job_id=_queue_search(conn, search))

    @r.put("/searches/{search_id}", response_model=SearchSaved)
    def update_search(
        search_id: int, body: watches.SearchIn, request: Request, _: Owner, conn: RW
    ) -> SearchSaved:
        search = watches.update_search(conn, _cfg(request), search_id, body, today=_today())
        return SearchSaved(search=search, job_id=_queue_search(conn, search))

    @r.put("/searches/{search_id}/status", response_model=SearchSaved)
    def search_status(
        search_id: int, body: StatusIn, request: Request, _: Owner, conn: RW
    ) -> SearchSaved:
        status = _status(body, ("active", "paused", "archived"))
        search = watches.set_search_status(conn, _cfg(request), search_id, status, today=_today())
        return SearchSaved(search=search, job_id=_queue_search(conn, search))

    @r.post("/searches/preview", response_model=SearchPreviewOut)
    def preview_search(
        body: watches.SearchIn,
        request: Request,
        _: Owner,
        conn: RW,
        search_id: int | None = None,
    ) -> SearchPreviewOut:
        """How many date pairs the search would track, saved as active, and how long a
        full detail refresh takes at the current budget. Saves nothing."""
        cfg = _cfg(request)
        p = watches.preview_search(conn, cfg, body, search_id=search_id, today=_today())
        per_day = fetch_mod.detail_fetches_per_day(conn, cfg)
        return SearchPreviewOut(
            pairs=p.pairs,
            total_pairs=p.total_pairs,
            max_pairs=watches.MAX_ACTIVE_PAIRS,
            detail_per_day=per_day,
            refresh_days=max(1, -(-p.total_pairs // per_day)),
        )

    @r.delete("/searches/{search_id}", response_model=OkOut)
    def delete_search(search_id: int, request: Request, _: Owner, conn: RW) -> OkOut:
        watches.delete_search(conn, _cfg(request), search_id, today=_today())
        return OkOut(ok=True)

    # -- deal watches -----------------------------------------------------------

    @r.get("/deal-watches", response_model=list[watches.DealWatch])
    def list_deal_watches(_: Owner, conn: RW) -> list[watches.DealWatch]:
        return watches.list_deal_watches(conn)

    @r.post("/deal-watches", response_model=watches.DealWatch, status_code=201)
    def create_deal_watch(body: watches.DealWatchIn, _: Owner, conn: RW) -> watches.DealWatch:
        return watches.create_deal_watch(conn, body)

    @r.post("/deal-watches/test", response_model=DealTestOut)
    def test_deal_watch(body: DealTestIn, _: Owner, conn: RW) -> DealTestOut:
        """Which deal watch, trip or flexible search a headline would alert for, and the
        price and travel months read from it."""
        item = deals.FeedItem(
            guid="test", title=body.headline, link=None, published=None, summary="", feed="test"
        )
        active = watches.deal_matchers(conn)
        hit = deals.first_match(item, active)
        named = deals.months_in(body.headline)
        return DealTestOut(
            price=deals.price_from_text(body.headline),
            months=sorted(named) if named else [],
            matched=hit.name if hit else None,
            matches=list(dict.fromkeys(w.name for w in active if deals.matches(item, w))),
        )

    @r.put("/deal-watches/{watch_id}", response_model=watches.DealWatch)
    def update_deal_watch(
        watch_id: int, body: watches.DealWatchIn, _: Owner, conn: RW
    ) -> watches.DealWatch:
        return watches.update_deal_watch(conn, watch_id, body)

    @r.put("/deal-watches/{watch_id}/status", response_model=watches.DealWatch)
    def deal_watch_status(watch_id: int, body: StatusIn, _: Owner, conn: RW) -> watches.DealWatch:
        status = _status(body, ("active", "paused", "archived"))
        return watches.set_deal_watch_status(conn, watch_id, status)

    @r.delete("/deal-watches/{watch_id}", response_model=OkOut)
    def delete_deal_watch(watch_id: int, _: Owner, conn: RW) -> OkOut:
        watches.delete_deal_watch(conn, watch_id)
        return OkOut(ok=True)

    # -- deal feeds ---------------------------------------------------------------

    @r.get("/deal-feeds", response_model=list[watches.DealFeed])
    def list_deal_feeds(_: Owner, conn: RW) -> list[watches.DealFeed]:
        return watches.list_deal_feeds(conn)

    @r.post("/deal-feeds", response_model=watches.DealFeed, status_code=201)
    def create_deal_feed(
        body: watches.DealFeedIn, request: Request, _: Owner, conn: RW
    ) -> watches.DealFeed:
        """Fetches the address once first and refuses one that is not a feed."""
        return watches.create_deal_feed(conn, body, check=_feed_check(request))

    @r.put("/deal-feeds/{feed_id}", response_model=watches.DealFeed)
    def update_deal_feed(
        feed_id: int, body: watches.DealFeedIn, request: Request, _: Owner, conn: RW
    ) -> watches.DealFeed:
        """A changed address is fetched once, as when adding a feed."""
        return watches.update_deal_feed(conn, feed_id, body, check=_feed_check(request))

    @r.put("/deal-feeds/{feed_id}/status", response_model=watches.DealFeed)
    def deal_feed_status(feed_id: int, body: StatusIn, _: Owner, conn: RW) -> watches.DealFeed:
        status = _status(body, ("active", "paused", "archived"))
        return watches.set_deal_feed_status(conn, feed_id, status)

    @r.delete("/deal-feeds/{feed_id}", response_model=OkOut)
    def delete_deal_feed(feed_id: int, _: Owner, conn: RW) -> OkOut:
        watches.delete_deal_feed(conn, feed_id)
        return OkOut(ok=True)

    # -- settings -------------------------------------------------------------------

    @r.get("/settings", response_model=app_settings.AlertSettings)
    def get_settings(request: Request, _: Owner, conn: RW) -> app_settings.AlertSettings:
        """The alert thresholds in force (stored here, else config/flighttrack.yaml)."""
        return app_settings.alert_settings(conn, _cfg(request))

    @r.put("/settings", response_model=app_settings.AlertSettings)
    def put_settings(
        body: app_settings.AlertSettings, request: Request, _: Owner, conn: RW
    ) -> app_settings.AlertSettings:
        app_settings.save_alert_settings(conn, body)
        return app_settings.alert_settings(conn, _cfg(request))

    # -- collecting ---------------------------------------------------------------------

    @r.get("/collecting", response_model=CollectingOut)
    def collecting(request: Request, _: Owner, conn: RW) -> CollectingOut:
        """Limits, today's request budget and any cooldown, for the Manage page."""
        cfg = _cfg(request)
        now = clock_now()
        used = fetch_mod.budget_used(conn, now)
        limit = pace.allowance(conn, cfg)
        c = watches.counts(conn)
        return CollectingOut(
            cooldown_until=fetch_mod.cooldown_active(conn, now),
            budget=BudgetOut(used=used, limit=limit, remaining=max(0, limit - used)),
            detail_per_day=fetch_mod.detail_fetches_per_day(conn, cfg),
            active_trips=c["trips"],
            max_trips=watches.MAX_ACTIVE_TRIPS,
            active_searches=c["searches"],
            max_searches=watches.MAX_ACTIVE_SEARCHES,
            active_pairs=c["pairs"],
            max_pairs=watches.MAX_ACTIVE_PAIRS,
            default_feeds=list(cfg.deals.feeds),
            regions=sorted(deals.REGIONS),
        )

    # -- jobs -------------------------------------------------------------------------

    @r.post("/jobs", response_model=JobOut, status_code=201)
    def create_job(body: JobIn, _: Owner, conn: RW) -> JobOut:
        """ "Check now": queue work for the worker, which picks it up within 15 seconds."""
        payload: dict[str, Any] = {}
        if body.kind in ("fetch_trip", "fetch_search", "run_batch"):
            _cooldown_check(conn)
        if body.kind == "fetch_trip":
            if not body.trip:
                raise watches.WatchError("trip: say which trip to fetch")
            payload["trip"] = watches.get_trip(conn, body.trip).slug
        elif body.kind == "fetch_search":
            if not body.search:
                raise watches.WatchError("search: say which search to fetch")
            payload["search"] = watches.get_search(conn, body.search).slug
        return _job(conn, jobs.enqueue(conn, body.kind, payload, "owner"))

    @r.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: int, _: Owner, conn: RW) -> JobOut:
        return _job(conn, job_id)

    return r
