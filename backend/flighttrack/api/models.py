"""Response models for the API. Prices are integer cents; times are ISO 8601 UTC
(`2027-01-15T10:00:00Z`); dates are `YYYY-MM-DD`.

`flighttrack schema --out` writes every shape here as one JSON Schema
(`ApiTypes`), from which the frontend's TypeScript types are generated.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..app_settings import AlertSettings
from ..watches import (
    DealFeed,
    DealFeedIn,
    DealWatch,
    DealWatchIn,
    Search,
    SearchIn,
    Trip,
    TripIn,
)
from .auth import LoginIn, Me

# ---------------------------------------------------------------------------
# small pieces
# ---------------------------------------------------------------------------


class PricePoint(BaseModel):
    at: str
    price: int


class ProblemOut(BaseModel):
    code: str
    severity: Literal["crit", "warn"]
    message: str


class NotificationOut(BaseModel):
    id: int
    kind: str  # price | deal | health
    title: str
    body: str
    url: str | None
    created_at: str


class DealOut(BaseModel):
    id: int
    title: str
    link: str | None
    price: int | None
    watch: str | None
    feed: str
    published_at: str | None
    seen_at: str


class ExplorePlaceOut(BaseModel):
    """A place on an Explore board: its cheapest current fare over the board's weeks."""

    place_id: str = Field(description="Google's id for the place; stable across replies")
    name: str
    country: str | None
    airport: str | None = Field(description="where the cheapest itinerary lands")
    price: int = Field(description="cents; an estimate (basic economy included)")
    currency: str
    depart_date: str
    return_date: str | None = Field(description="null for one way")
    airline: str | None
    stops: int | None
    duration_min: int | None
    observed_at: str
    link: str | None = Field(description="Google Flights for these dates")
    under_max: bool = Field(description="at or under the region watch's price")


class ExploreBoardOut(BaseModel):
    """A region priced from an airport for a trip length, every week ahead."""

    key: str = Field(description="'anywhere-7' for a built-in board, 'watch-<id>' for a watch")
    title: str
    origin: str
    region: str = Field(description="the Explore place asked: anywhere, europe, asia, …")
    nights: int | None = Field(description="trip length; null for one way")
    months: list[int] = Field(description="travel months priced; [] = every month")
    watch_id: int | None
    max_price: int | None
    departures: int = Field(description="departure dates the board prices")
    priced_departures: int = Field(description="of those, with a current fare for some place")
    updated_at: str | None
    places: list[ExplorePlaceOut]


class AirportOut(BaseModel):
    iata: str
    name: str
    city: str
    country: str


# ---------------------------------------------------------------------------
# health and meta
# ---------------------------------------------------------------------------


class LivezOut(BaseModel):
    ok: bool


class HealthOut(BaseModel):
    """`/api/health`: 200 when `status` is ok, 503 otherwise."""

    status: Literal["ok", "stale", "no_data", "worker_stopped"]
    newest_price_at: str | None
    age_s: int | None
    max_age_s: int
    worker_heartbeat_age_s: int | None


class MetaOut(BaseModel):
    version: str
    commit: str | None
    home_airport: str
    currency: str
    owner_login_enabled: bool
    push_enabled: bool


class HealthSummary(BaseModel):
    ok: bool
    problems: list[ProblemOut]
    last_success_at: str | None
    cooldown_until: str | None


# ---------------------------------------------------------------------------
# trips, searches, date pairs
# ---------------------------------------------------------------------------


class VerdictOut(BaseModel):
    """`report.advise` for one date pair: arithmetic on the history, not a forecast."""

    verdict: Literal["BOOK", "LEAN BOOK", "HOLD", "WAIT"]
    reasons: list[str]
    days_to_departure: int
    observations: int
    all_time_low: int | None
    route_p20: int | None
    route_p50: int | None
    change_7d_pct: float | None


class TripSummary(BaseModel):
    slug: str
    name: str
    origin: str
    destination: str
    label: str
    depart_date: str
    return_date: str | None
    status: str
    target_price: int | None
    max_stops: int | None
    price: int | None = Field(description="the newest price; None until the first fetch")
    price_at: str | None
    change_7d_pct: float | None
    verdict: str | None = Field(description="BOOK, LEAN BOOK, HOLD or WAIT; None when not active")
    all_time_low: int | None
    low_30d: int | None
    observations: int
    days_to_departure: int
    series: list[PricePoint] = Field(description="the newest points, oldest first")
    link: str | None


class LayoverOut(BaseModel):
    minutes: int | None
    airport: str | None


class SegmentOut(BaseModel):
    origin: str | None
    destination: str | None
    departs: str | None  # local time at the airport, "YYYY-MM-DD HH:MM"
    arrives: str | None
    minutes: int | None
    flight: str | None
    aircraft: str | None


class OfferOut(BaseModel):
    """One itinerary from the newest fetch."""

    price: int
    airline: str | None
    stops: int | None
    duration_min: int | None
    departs: str | None
    arrives: str | None
    layovers: list[LayoverOut]
    segments: list[SegmentOut]
    source: str | None


class AlertOut(BaseModel):
    sent_at: str
    reason: str
    price: int


class TripOut(TripSummary):
    id: int
    notes: str | None
    query_id: int | None
    offers: list[OfferOut]
    offers_at: str | None
    advice: VerdictOut | None
    alerts: list[AlertOut]


class DatePairOut(BaseModel):
    query_id: int
    origin: str
    destination: str
    depart_date: str
    return_date: str | None
    price: int | None
    source: Literal["detail", "calendar"] | None = Field(
        description="detail = a results-page fetch; calendar = the price graph, an estimate"
    )
    price_at: str | None
    age_hours: float | None
    low_30d: int | None
    calendar_price: int | None
    calendar_at: str | None
    airline: str | None
    stops: int | None
    duration_min: int | None
    target_price: int | None
    flags: list[str] = Field(description="cheapest, under_target, at_low, estimate")
    link: str | None


class SearchSummary(BaseModel):
    slug: str
    name: str
    origin: str
    destinations: list[str]
    depart_dows: list[str]
    nights: int
    status: str
    target_price: int | None
    pairs: int
    priced: int
    best: DatePairOut | None


class DestinationOut(BaseModel):
    code: str
    label: str
    pairs: list[DatePairOut]


class SearchOut(SearchSummary):
    id: int
    window_start: str | None
    window_end: str | None
    min_days_ahead: int | None
    max_stops: int | None
    priority: int
    grid: list[DestinationOut]


class HistoryOut(BaseModel):
    query_id: int
    origin: str
    destination: str
    depart_date: str
    return_date: str | None
    active: bool
    target_price: int | None
    points: list[PricePoint]
    calendar: list[PricePoint]
    link: str | None


class OverviewOut(BaseModel):
    generated_at: str
    home_airport: str
    trips: list[TripSummary]
    searches: list[SearchSummary]
    notifications: list[NotificationOut]
    deals: list[DealOut]
    health: HealthSummary


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class RunOut(BaseModel):
    id: int
    kind: str
    started_at: str
    finished_at: str | None
    attempted: int
    succeeded: int
    failed: int
    blocked: bool
    skipped: bool
    notes: str | None


class RequestDay(BaseModel):
    day: str
    outcomes: dict[str, int]


class BudgetOut(BaseModel):
    used: int
    limit: int
    remaining: int


class FeedOut(BaseModel):
    url: str
    name: str | None
    last_at: str | None
    last_ok_at: str | None
    last_items: int | None
    last_error: str | None


class BackupOut(BaseModel):
    name: str
    bytes: int
    created_at: str


class CanaryOut(BaseModel):
    at: str | None
    result: str | None
    detail: str | None


class PaceOut(BaseModel):
    """How many requests to Google a day, as the worker has learned it (pace.py)."""

    per_day: int = Field(description="Google requests allowed in any rolling 24 hours, now")
    ceiling: int = Field(description="the most it may rise to (collect.pace.max_per_day)")
    fixed: bool = Field(description="pinned by collect.daily_request_budget; nothing learned")
    reason: str = Field(description="why it is at this number, in a few words")
    changed_at: str | None
    hold_until: str | None = Field(description="held after a block until then")
    last_block_at: str | None
    next_review_at: str | None = Field(description="the earliest it can rise again")
    gap_seconds: int = Field(description="the average gap between slots at this pace")


class LaneOut(BaseModel):
    """One kind of Google request and its share of the pace."""

    name: Literal["detail", "calendar", "explore"]
    enabled: bool = Field(description="this lane can run with this setup")
    share: float = Field(description="its fraction of the pace while every enabled lane has work")
    used: int = Field(description="its requests in the last 24 hours")
    paused_until: str | None = Field(description="Google declined its calls; paused until then")
    pause_reason: str | None
    last_outcome: str | None
    last_at: str | None


class StatusOut(BaseModel):
    generated_at: str
    version: str
    commit: str | None
    ok: bool
    checked_at: str
    problems: list[ProblemOut]
    info: list[str]
    last_success_at: str | None
    newest_price_at: str | None
    cooldown_until: str | None
    blocked_streak: int
    next_batch_at: str | None
    last_sweep_at: str | None
    budget: BudgetOut
    pace: PaceOut
    lanes: list[LaneOut]
    calendar_windows: int = Field(description="calendar windows the flexible searches need")
    calendar_windows_due: int = Field(description="of those, not priced within the refresh time")
    explore_departures: int = Field(description="Explore calls the region boards need")
    explore_departures_due: int = Field(
        description="of those, not priced within explore.refresh_hours"
    )
    requests_by_day: list[RequestDay]
    recent_runs: list[RunOut]
    feeds: list[FeedOut]
    canary: CanaryOut | None
    backups: list[BackupOut]
    database_bytes: int
    observations: int
    active_pairs: int
    jobs_queued: int
    worker_heartbeat_age_s: int | None


# ---------------------------------------------------------------------------
# admin
# ---------------------------------------------------------------------------


class StatusIn(BaseModel):
    status: Literal["active", "paused", "booked", "archived"]


class TripSaved(BaseModel):
    trip: Trip
    job_id: int | None = Field(description="the fetch job queued for it, if it is active")


class SearchSaved(BaseModel):
    search: Search
    job_id: int | None


class OkOut(BaseModel):
    ok: bool


class SearchPreviewOut(BaseModel):
    """What saving a search would track, before it is saved."""

    pairs: int = Field(description="date pairs this search would track")
    total_pairs: int = Field(description="date pairs tracked in all, with it saved as active")
    max_pairs: int = Field(description="the limit on active date pairs")
    detail_per_day: int = Field(description="detail fetches a day at the current budget")
    refresh_days: int = Field(description="days a detail fetch of every tracked pair takes")


class CollectingOut(BaseModel):
    """What the Manage page shows about collecting: limits, budget and any cooldown."""

    cooldown_until: str | None = Field(description="collecting is paused until then")
    budget: BudgetOut
    detail_per_day: int
    active_trips: int
    max_trips: int
    active_searches: int
    max_searches: int
    active_pairs: int
    max_pairs: int
    default_feeds: list[str] = Field(description="polled while no feed has been added in the app")
    regions: list[str] = Field(
        description="region names a deal watch's destinations may use; each stands for a list "
        "of place names"
    )


class DealTestIn(BaseModel):
    headline: str = Field(min_length=1, max_length=500)


class DealTestOut(BaseModel):
    price: int | None
    months: list[int] = Field(description="travel months named in the headline, 1-12")
    matched: str | None = Field(description="the watch that would alert, if any")
    matches: list[str] = Field(description="every active watch the headline matches")


class JobIn(BaseModel):
    kind: Literal["fetch_trip", "fetch_search", "poll_deals", "run_batch"]
    trip: str | None = Field(default=None, description="slug, for fetch_trip")
    search: str | None = Field(default=None, description="slug, for fetch_search")


class JobOut(BaseModel):
    id: int
    kind: str
    payload: dict[str, Any]
    status: Literal["queued", "running", "done", "failed"]
    requested_by: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None


class ApiTypes(BaseModel):
    """Every request and response shape, exported as one JSON Schema for the frontend."""

    livez: LivezOut
    health: HealthOut
    meta: MetaOut
    overview: OverviewOut
    trip: TripOut
    search: SearchOut
    history: HistoryOut
    deals: list[DealOut]
    explore: list[ExploreBoardOut]
    status: StatusOut
    airports: list[AirportOut]
    me: Me
    login_in: LoginIn
    trip_in: TripIn
    admin_trips: list[Trip]
    trip_saved: TripSaved
    search_in: SearchIn
    admin_searches: list[Search]
    search_saved: SearchSaved
    deal_watch_in: DealWatchIn
    admin_deal_watches: list[DealWatch]
    deal_feed_in: DealFeedIn
    admin_deal_feeds: list[DealFeed]
    status_in: StatusIn
    deal_test_in: DealTestIn
    deal_test_out: DealTestOut
    alert_settings: AlertSettings
    job_in: JobIn
    job: JobOut
    ok: OkOut
    search_preview: SearchPreviewOut
    collecting: CollectingOut
