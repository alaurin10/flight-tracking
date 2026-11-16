// Generated from backend/flighttrack/api/models.py by `npm run types`. Do not edit.

/**
 * Every request and response shape, exported as one JSON Schema for the frontend.
 */
export interface ApiTypes {
	admin_deal_feeds: DealFeed[];
	admin_deal_watches: DealWatch[];
	admin_searches: Search[];
	admin_trips: Trip[];
	airports: AirportOut[];
	alert_settings: AlertSettings;
	collecting: CollectingOut;
	deal_feed_in: DealFeedIn;
	deal_test_in: DealTestIn;
	deal_test_out: DealTestOut;
	deal_watch_in: DealWatchIn;
	deals: DealOut[];
	health: HealthOut;
	history: HistoryOut;
	job: JobOut;
	job_in: JobIn;
	livez: LivezOut;
	login_in: LoginIn;
	me: Me;
	meta: MetaOut;
	ok: OkOut;
	overview: OverviewOut;
	search: SearchOut;
	search_in: SearchIn;
	search_preview: SearchPreviewOut;
	search_saved: SearchSaved;
	status: StatusOut;
	status_in: StatusIn;
	trip: TripOut;
	trip_in: TripIn;
	trip_saved: TripSaved;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DealFeed".
 */
export interface DealFeed {
	created_at: string;
	id: number;
	/**
	 * feed (RSS or Atom) or page (a web page read as a list of posts); found by fetching it once when it is saved
	 */
	kind?: 'feed' | 'page';
	name?: string | null;
	status?: 'active' | 'paused' | 'archived';
	url: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DealWatch".
 */
export interface DealWatch {
	created_at: string;
	/**
	 * @maxItems 20
	 */
	destinations?:
		| []
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string, string]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ];
	id: number;
	/**
	 * @maxItems 10
	 */
	keywords?:
		| []
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string];
	max_price?: number | null;
	/**
	 * travel months, 1-12; a post that names months must name one of these
	 *
	 * @maxItems 12
	 */
	months?:
		| []
		| [number]
		| [number, number]
		| [number, number, number]
		| [number, number, number, number]
		| [number, number, number, number, number]
		| [number, number, number, number, number, number]
		| [number, number, number, number, number, number, number]
		| [number, number, number, number, number, number, number, number]
		| [number, number, number, number, number, number, number, number, number]
		| [number, number, number, number, number, number, number, number, number, number]
		| [number, number, number, number, number, number, number, number, number, number, number]
		| [
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number
		  ];
	name: string;
	/**
	 * @maxItems 20
	 */
	origins?:
		| []
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string, string]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ];
	status?: 'active' | 'paused' | 'archived';
	updated_at: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "Search".
 */
export interface Search {
	created_at: string;
	/**
	 * @minItems 1
	 * @maxItems 7
	 */
	depart_dows:
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string];
	/**
	 * @minItems 1
	 * @maxItems 10
	 */
	destinations:
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string];
	id: number;
	max_stops?: number | null;
	min_days_ahead?: number | null;
	name: string;
	nights: number;
	origin: string;
	priority?: number;
	slug: string;
	status?: 'active' | 'paused' | 'archived';
	target_price?: number | null;
	updated_at: string;
	window_end?: string | null;
	window_start?: string | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "Trip".
 */
export interface Trip {
	created_at: string;
	depart_date: string;
	destination: string;
	id: number;
	max_stops?: number | null;
	name: string;
	notes?: string | null;
	origin: string;
	return_date?: string | null;
	slug: string;
	status?: 'active' | 'paused' | 'booked' | 'archived';
	target_price?: number | null;
	updated_at: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "AirportOut".
 */
export interface AirportOut {
	city: string;
	country: string;
	iata: string;
	name: string;
}
/**
 * Alert thresholds the owner can change.
 *
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "AlertSettings".
 */
export interface AlertSettings {
	/**
	 * days before a date pair alerts again
	 */
	dedup_days: number;
	/**
	 * inside that window, alert again after a drop this large
	 */
	dedup_further_drop_pct: number;
	/**
	 * alert on prices in the route's cheapest 20%
	 */
	percentile_alerts: boolean;
}
/**
 * What the Manage page shows about collecting: limits, budget and any cooldown.
 *
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "CollectingOut".
 */
export interface CollectingOut {
	active_pairs: number;
	active_searches: number;
	active_trips: number;
	budget: BudgetOut;
	/**
	 * collecting is paused until then
	 */
	cooldown_until: string | null;
	/**
	 * polled while no feed has been added in the app
	 */
	default_feeds: string[];
	detail_per_day: number;
	max_pairs: number;
	max_searches: number;
	max_trips: number;
	/**
	 * region names a deal watch's destinations may use; each stands for a list of place names
	 */
	regions: string[];
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "BudgetOut".
 */
export interface BudgetOut {
	limit: number;
	remaining: number;
	used: number;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DealFeedIn".
 */
export interface DealFeedIn {
	/**
	 * feed (RSS or Atom) or page (a web page read as a list of posts); found by fetching it once when it is saved
	 */
	kind?: 'feed' | 'page';
	name?: string | null;
	status?: 'active' | 'paused' | 'archived';
	url: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DealTestIn".
 */
export interface DealTestIn {
	headline: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DealTestOut".
 */
export interface DealTestOut {
	/**
	 * the watch that would alert, if any
	 */
	matched: string | null;
	/**
	 * every active watch the headline matches
	 */
	matches: string[];
	/**
	 * travel months named in the headline, 1-12
	 */
	months: number[];
	price: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DealWatchIn".
 */
export interface DealWatchIn {
	/**
	 * @maxItems 20
	 */
	destinations?:
		| []
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string, string]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ];
	/**
	 * @maxItems 10
	 */
	keywords?:
		| []
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string];
	max_price?: number | null;
	/**
	 * travel months, 1-12; a post that names months must name one of these
	 *
	 * @maxItems 12
	 */
	months?:
		| []
		| [number]
		| [number, number]
		| [number, number, number]
		| [number, number, number, number]
		| [number, number, number, number, number]
		| [number, number, number, number, number, number]
		| [number, number, number, number, number, number, number]
		| [number, number, number, number, number, number, number, number]
		| [number, number, number, number, number, number, number, number, number]
		| [number, number, number, number, number, number, number, number, number, number]
		| [number, number, number, number, number, number, number, number, number, number, number]
		| [
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number,
				number
		  ];
	name: string;
	/**
	 * @maxItems 20
	 */
	origins?:
		| []
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string, string]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ]
		| [
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string,
				string
		  ];
	status?: 'active' | 'paused' | 'archived';
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DealOut".
 */
export interface DealOut {
	feed: string;
	id: number;
	link: string | null;
	price: number | null;
	published_at: string | null;
	seen_at: string;
	title: string;
	watch: string | null;
}
/**
 * `/api/health`: 200 when `status` is ok, 503 otherwise.
 *
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "HealthOut".
 */
export interface HealthOut {
	age_s: number | null;
	max_age_s: number;
	newest_price_at: string | null;
	status: 'ok' | 'stale' | 'no_data' | 'worker_stopped';
	worker_heartbeat_age_s: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "HistoryOut".
 */
export interface HistoryOut {
	active: boolean;
	calendar: PricePoint[];
	depart_date: string;
	destination: string;
	link: string | null;
	origin: string;
	points: PricePoint[];
	query_id: number;
	return_date: string | null;
	target_price: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "PricePoint".
 */
export interface PricePoint {
	at: string;
	price: number;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "JobOut".
 */
export interface JobOut {
	created_at: string;
	error: string | null;
	finished_at: string | null;
	id: number;
	kind: string;
	payload: {
		[k: string]: unknown;
	};
	requested_by: string | null;
	started_at: string | null;
	status: 'queued' | 'running' | 'done' | 'failed';
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "JobIn".
 */
export interface JobIn {
	kind: 'fetch_trip' | 'fetch_search' | 'poll_deals' | 'run_batch';
	/**
	 * slug, for fetch_search
	 */
	search?: string | null;
	/**
	 * slug, for fetch_trip
	 */
	trip?: string | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "LivezOut".
 */
export interface LivezOut {
	ok: boolean;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "LoginIn".
 */
export interface LoginIn {
	password: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "Me".
 */
export interface Me {
	is_owner: boolean;
	owner_login_enabled: boolean;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "MetaOut".
 */
export interface MetaOut {
	commit: string | null;
	currency: string;
	home_airport: string;
	owner_login_enabled: boolean;
	push_enabled: boolean;
	version: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "OkOut".
 */
export interface OkOut {
	ok: boolean;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "OverviewOut".
 */
export interface OverviewOut {
	deals: DealOut[];
	generated_at: string;
	health: HealthSummary;
	home_airport: string;
	notifications: NotificationOut[];
	searches: SearchSummary[];
	trips: TripSummary[];
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "HealthSummary".
 */
export interface HealthSummary {
	cooldown_until: string | null;
	last_success_at: string | null;
	ok: boolean;
	problems: ProblemOut[];
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "ProblemOut".
 */
export interface ProblemOut {
	code: string;
	message: string;
	severity: 'crit' | 'warn';
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "NotificationOut".
 */
export interface NotificationOut {
	body: string;
	created_at: string;
	id: number;
	kind: string;
	title: string;
	url: string | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "SearchSummary".
 */
export interface SearchSummary {
	best: DatePairOut | null;
	depart_dows: string[];
	destinations: string[];
	name: string;
	nights: number;
	origin: string;
	pairs: number;
	priced: number;
	slug: string;
	status: string;
	target_price: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DatePairOut".
 */
export interface DatePairOut {
	age_hours: number | null;
	airline: string | null;
	calendar_at: string | null;
	calendar_price: number | null;
	depart_date: string;
	destination: string;
	duration_min: number | null;
	/**
	 * cheapest, under_target, at_low, estimate
	 */
	flags: string[];
	link: string | null;
	low_30d: number | null;
	origin: string;
	price: number | null;
	price_at: string | null;
	query_id: number;
	return_date: string | null;
	/**
	 * detail = a results-page fetch; calendar = the price graph, an estimate
	 */
	source: ('detail' | 'calendar') | null;
	stops: number | null;
	target_price: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "TripSummary".
 */
export interface TripSummary {
	all_time_low: number | null;
	change_7d_pct: number | null;
	days_to_departure: number;
	depart_date: string;
	destination: string;
	label: string;
	link: string | null;
	low_30d: number | null;
	max_stops: number | null;
	name: string;
	observations: number;
	origin: string;
	/**
	 * the newest price; None until the first fetch
	 */
	price: number | null;
	price_at: string | null;
	return_date: string | null;
	/**
	 * the newest points, oldest first
	 */
	series: PricePoint[];
	slug: string;
	status: string;
	target_price: number | null;
	/**
	 * BOOK, LEAN BOOK, HOLD or WAIT; None when not active
	 */
	verdict: string | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "SearchOut".
 */
export interface SearchOut {
	best: DatePairOut | null;
	depart_dows: string[];
	destinations: string[];
	grid: DestinationOut[];
	id: number;
	max_stops: number | null;
	min_days_ahead: number | null;
	name: string;
	nights: number;
	origin: string;
	pairs: number;
	priced: number;
	priority: number;
	slug: string;
	status: string;
	target_price: number | null;
	window_end: string | null;
	window_start: string | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "DestinationOut".
 */
export interface DestinationOut {
	code: string;
	label: string;
	pairs: DatePairOut[];
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "SearchIn".
 */
export interface SearchIn {
	/**
	 * @minItems 1
	 * @maxItems 7
	 */
	depart_dows:
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string];
	/**
	 * @minItems 1
	 * @maxItems 10
	 */
	destinations:
		| [string]
		| [string, string]
		| [string, string, string]
		| [string, string, string, string]
		| [string, string, string, string, string]
		| [string, string, string, string, string, string]
		| [string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string]
		| [string, string, string, string, string, string, string, string, string, string];
	max_stops?: number | null;
	min_days_ahead?: number | null;
	name: string;
	nights: number;
	origin: string;
	priority?: number;
	slug?: string | null;
	status?: 'active' | 'paused' | 'archived';
	target_price?: number | null;
	window_end?: string | null;
	window_start?: string | null;
}
/**
 * What saving a search would track, before it is saved.
 *
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "SearchPreviewOut".
 */
export interface SearchPreviewOut {
	/**
	 * detail fetches a day at the current budget
	 */
	detail_per_day: number;
	/**
	 * the limit on active date pairs
	 */
	max_pairs: number;
	/**
	 * date pairs this search would track
	 */
	pairs: number;
	/**
	 * days a detail fetch of every tracked pair takes
	 */
	refresh_days: number;
	/**
	 * date pairs tracked in all, with it saved as active
	 */
	total_pairs: number;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "SearchSaved".
 */
export interface SearchSaved {
	job_id: number | null;
	search: Search;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "StatusOut".
 */
export interface StatusOut {
	active_pairs: number;
	backups: BackupOut[];
	blocked_streak: number;
	budget: BudgetOut;
	canary: CanaryOut | null;
	checked_at: string;
	commit: string | null;
	cooldown_until: string | null;
	database_bytes: number;
	feeds: FeedOut[];
	generated_at: string;
	info: string[];
	jobs_queued: number;
	last_success_at: string | null;
	last_sweep_at: string | null;
	newest_price_at: string | null;
	next_batch_at: string | null;
	observations: number;
	ok: boolean;
	problems: ProblemOut[];
	recent_runs: RunOut[];
	requests_by_day: RequestDay[];
	version: string;
	worker_heartbeat_age_s: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "BackupOut".
 */
export interface BackupOut {
	bytes: number;
	created_at: string;
	name: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "CanaryOut".
 */
export interface CanaryOut {
	at: string | null;
	detail: string | null;
	result: string | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "FeedOut".
 */
export interface FeedOut {
	last_at: string | null;
	last_error: string | null;
	last_items: number | null;
	last_ok_at: string | null;
	name: string | null;
	url: string;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "RunOut".
 */
export interface RunOut {
	attempted: number;
	blocked: boolean;
	failed: number;
	finished_at: string | null;
	id: number;
	kind: string;
	notes: string | null;
	skipped: boolean;
	started_at: string;
	succeeded: number;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "RequestDay".
 */
export interface RequestDay {
	day: string;
	outcomes: {
		[k: string]: number;
	};
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "StatusIn".
 */
export interface StatusIn {
	status: 'active' | 'paused' | 'booked' | 'archived';
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "TripOut".
 */
export interface TripOut {
	advice: VerdictOut | null;
	alerts: AlertOut[];
	all_time_low: number | null;
	change_7d_pct: number | null;
	days_to_departure: number;
	depart_date: string;
	destination: string;
	id: number;
	label: string;
	link: string | null;
	low_30d: number | null;
	max_stops: number | null;
	name: string;
	notes: string | null;
	observations: number;
	offers: OfferOut[];
	offers_at: string | null;
	origin: string;
	/**
	 * the newest price; None until the first fetch
	 */
	price: number | null;
	price_at: string | null;
	query_id: number | null;
	return_date: string | null;
	/**
	 * the newest points, oldest first
	 */
	series: PricePoint[];
	slug: string;
	status: string;
	target_price: number | null;
	/**
	 * BOOK, LEAN BOOK, HOLD or WAIT; None when not active
	 */
	verdict: string | null;
}
/**
 * `report.advise` for one date pair: arithmetic on the history, not a forecast.
 *
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "VerdictOut".
 */
export interface VerdictOut {
	all_time_low: number | null;
	change_7d_pct: number | null;
	days_to_departure: number;
	observations: number;
	reasons: string[];
	route_p20: number | null;
	route_p50: number | null;
	verdict: 'BOOK' | 'LEAN BOOK' | 'HOLD' | 'WAIT';
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "AlertOut".
 */
export interface AlertOut {
	price: number;
	reason: string;
	sent_at: string;
}
/**
 * One itinerary from the newest fetch.
 *
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "OfferOut".
 */
export interface OfferOut {
	airline: string | null;
	arrives: string | null;
	departs: string | null;
	duration_min: number | null;
	layovers: LayoverOut[];
	price: number;
	segments: SegmentOut[];
	source: string | null;
	stops: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "LayoverOut".
 */
export interface LayoverOut {
	airport: string | null;
	minutes: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "SegmentOut".
 */
export interface SegmentOut {
	aircraft: string | null;
	arrives: string | null;
	departs: string | null;
	destination: string | null;
	flight: string | null;
	minutes: number | null;
	origin: string | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "TripIn".
 */
export interface TripIn {
	depart_date: string;
	destination: string;
	max_stops?: number | null;
	name: string;
	notes?: string | null;
	origin: string;
	return_date?: string | null;
	slug?: string | null;
	status?: 'active' | 'paused' | 'booked' | 'archived';
	target_price?: number | null;
}
/**
 * This interface was referenced by `ApiTypes`'s JSON-Schema
 * via the `definition` "TripSaved".
 */
export interface TripSaved {
	/**
	 * the fetch job queued for it, if it is active
	 */
	job_id: number | null;
	trip: Trip;
}
