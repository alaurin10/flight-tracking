// The API client. Every page loads through here; errors arrive as ApiError, with status 0
// when the server could not be reached at all. The owner's calls (sign-in and /api/admin)
// are at the end; the browser sends the session cookie and this site's Origin itself.
import type {
	AirportOut,
	AlertSettings,
	CollectingOut,
	DealFeed,
	DealFeedIn,
	DealTestOut,
	DealWatch,
	DealWatchIn,
	JobIn,
	JobOut,
	Me,
	OkOut,
	Search,
	SearchIn,
	SearchPreviewOut,
	SearchSaved,
	Trip,
	TripIn,
	TripSaved,
	DealOut,
	ExploreBoardOut,
	FindOut,
	HealthOut,
	HistoryOut,
	MetaOut,
	OverviewOut,
	SearchOut,
	StatusOut,
	TripOut
} from './types.gen';

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}

	/** The server didn't answer (offline, or it's down). */
	get unreachable(): boolean {
		return this.status === 0 || this.status === 502 || this.status === 504;
	}
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
	try {
		return await fetch(path, {
			...init,
			headers: { Accept: 'application/json', ...(init.headers ?? {}) }
		});
	} catch {
		throw new ApiError(0, 'Flight Watch could not be reached.');
	}
}

async function answer<T>(r: Response): Promise<T> {
	if (!r.ok) {
		let detail = r.statusText;
		try {
			detail = ((await r.json()) as { detail?: string }).detail ?? detail;
		} catch {
			// not JSON (a proxy's error page)
		}
		throw new ApiError(r.status, detail);
	}
	return (await r.json()) as T;
}

async function get<T>(path: string): Promise<T> {
	return answer<T>(await request(path));
}

async function send<T>(
	method: 'POST' | 'PUT' | 'DELETE',
	path: string,
	body?: unknown
): Promise<T> {
	const init: RequestInit = { method, cache: 'no-store' };
	if (body !== undefined) {
		init.body = JSON.stringify(body);
		init.headers = { 'Content-Type': 'application/json' };
	}
	return answer<T>(await request(path, init));
}

// The overview feeds both the page and the desktop rail; one copy serves both for a
// minute (prices change a few times a day at most).
let overview: { at: number; p: Promise<OverviewOut> } | null = null;
export function getOverview({ fresh = false } = {}): Promise<OverviewOut> {
	if (fresh || !overview || Date.now() - overview.at > 60_000) {
		const p = get<OverviewOut>('/api/overview');
		p.catch(() => (overview = null));
		overview = { at: Date.now(), p };
	}
	return overview.p;
}

/** After a change to what is tracked: the next overview read asks the server. */
export function forgetOverview(): void {
	overview = null;
}

export const getTrip = (slug: string) => get<TripOut>(`/api/trips/${encodeURIComponent(slug)}`);
export const getSearch = (slug: string) =>
	get<SearchOut>(`/api/searches/${encodeURIComponent(slug)}`);
export const getHistory = (queryId: number) => get<HistoryOut>(`/api/queries/${queryId}/history`);
export const getDeals = () => get<DealOut[]>('/api/deals');
export const getExplore = () => get<ExploreBoardOut[]>('/api/explore');
export const getFinds = () => get<FindOut[]>('/api/finds');
export const getStatus = () => get<StatusOut>('/api/status');

/** /api/health answers 503 when prices are stale or missing; that's still an answer. */
export async function getHealth(): Promise<HealthOut> {
	const r = await request('/api/health');
	try {
		return (await r.json()) as HealthOut;
	} catch {
		throw new ApiError(r.status, r.statusText);
	}
}

// Version and server settings; they don't change while the page is open.
let meta: Promise<MetaOut> | null = null;
export function getMeta(): Promise<MetaOut> {
	if (!meta) {
		meta = get<MetaOut>('/api/meta');
		meta.catch(() => (meta = null));
	}
	return meta;
}

export const searchAirports = (q: string) =>
	get<AirportOut[]>(`/api/airports?q=${encodeURIComponent(q)}`);

// -- the owner ---------------------------------------------------------------------------

export const getMe = () => get<Me>('/api/auth/me');
export const login = (password: string) => send<Me>('POST', '/api/auth/login', { password });
export const logout = () => send<Me>('POST', '/api/auth/logout');

export type WatchKind = 'trips' | 'searches' | 'deal-watches' | 'deal-feeds';
export type Status = 'active' | 'paused' | 'booked' | 'archived';

export const adminTrips = () => get<Trip[]>('/api/admin/trips');
export const adminSearches = () => get<Search[]>('/api/admin/searches');
export const adminDealWatches = () => get<DealWatch[]>('/api/admin/deal-watches');
export const adminDealFeeds = () => get<DealFeed[]>('/api/admin/deal-feeds');
export const getCollecting = () => get<CollectingOut>('/api/admin/collecting');

export const saveTrip = (body: TripIn, id?: number) =>
	id === undefined
		? send<TripSaved>('POST', '/api/admin/trips', body)
		: send<TripSaved>('PUT', `/api/admin/trips/${id}`, body);
export const saveSearch = (body: SearchIn, id?: number) =>
	id === undefined
		? send<SearchSaved>('POST', '/api/admin/searches', body)
		: send<SearchSaved>('PUT', `/api/admin/searches/${id}`, body);
export const previewSearch = (body: SearchIn, id?: number) =>
	send<SearchPreviewOut>(
		'POST',
		`/api/admin/searches/preview${id === undefined ? '' : `?search_id=${id}`}`,
		body
	);
export const saveDealWatch = (body: DealWatchIn, id?: number) =>
	id === undefined
		? send<DealWatch>('POST', '/api/admin/deal-watches', body)
		: send<DealWatch>('PUT', `/api/admin/deal-watches/${id}`, body);
export const testHeadline = (headline: string) =>
	send<DealTestOut>('POST', '/api/admin/deal-watches/test', { headline });
export const saveDealFeed = (body: DealFeedIn, id?: number) =>
	id === undefined
		? send<DealFeed>('POST', '/api/admin/deal-feeds', body)
		: send<DealFeed>('PUT', `/api/admin/deal-feeds/${id}`, body);

/** Pause, resume, archive or mark booked. Trips and searches answer `{trip|search, job_id}`. */
export const setStatus = (kind: WatchKind, id: number, status: Status) =>
	send<unknown>('PUT', `/api/admin/${kind}/${id}/status`, { status });
export const deleteWatch = (kind: WatchKind, id: number) =>
	send<OkOut>('DELETE', `/api/admin/${kind}/${id}`);

export const getAlertSettings = () => get<AlertSettings>('/api/admin/settings');
export const saveAlertSettings = (body: AlertSettings) =>
	send<AlertSettings>('PUT', '/api/admin/settings', body);

export const queueJob = (body: JobIn) => send<JobOut>('POST', '/api/admin/jobs', body);
export const getJob = (id: number) => get<JobOut>(`/api/admin/jobs/${id}`);

/** Poll a job every two seconds until it is done or failed (or three minutes pass). */
export async function waitForJob(
	id: number,
	onUpdate?: (j: JobOut) => void,
	signal?: AbortSignal,
	every = 2000
): Promise<JobOut> {
	let j = await getJob(id);
	for (let i = 0; i < 90; i++) {
		if (signal?.aborted) break;
		onUpdate?.(j);
		if (j.status === 'done' || j.status === 'failed') return j;
		await new Promise((r) => setTimeout(r, every));
		if (signal?.aborted) break;
		j = await getJob(id);
	}
	return j;
}
