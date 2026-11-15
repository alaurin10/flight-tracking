// The read-only API client. Every page loads through here; errors arrive as ApiError,
// with status 0 when the server could not be reached at all.
import type {
	DealOut,
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

async function get<T>(path: string): Promise<T> {
	const r = await request(path);
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

export const getTrip = (slug: string) => get<TripOut>(`/api/trips/${encodeURIComponent(slug)}`);
export const getSearch = (slug: string) =>
	get<SearchOut>(`/api/searches/${encodeURIComponent(slug)}`);
export const getHistory = (queryId: number) => get<HistoryOut>(`/api/queries/${queryId}/history`);
export const getDeals = () => get<DealOut[]>('/api/deals');
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
