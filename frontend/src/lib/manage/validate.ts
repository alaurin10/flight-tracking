// What the Manage forms check before anything is sent, and how a form's fields become
// the API's shapes. The server checks everything again (airport codes against its list,
// the limits); these checks only catch what can be told in the browser, so a mistake
// is shown beside its field at once.
//
// Money is typed in dollars ("612", "$1,250", "612.50") and sent in cents.
import type { AlertSettings, DealFeedIn, DealWatchIn, SearchIn, TripIn } from '../types.gen';

export type Errors = Record<string, string>;

export const DOWS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'] as const;
export type Dow = (typeof DOWS)[number];

const CODE = /^[A-Z]{3}$/;
const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** Dollars as typed → cents. Empty is `null` (no amount); anything unreadable is an error. */
export function parseDollars(text: string): { cents: number | null; error?: string } {
	const t = text.trim().replace(/^\$/, '').replace(/,/g, '').trim();
	if (!t) return { cents: null };
	if (!/^\d+(\.\d{1,2})?$/.test(t)) return { cents: null, error: 'Type an amount in dollars' };
	const cents = Math.round(Number(t) * 100);
	if (cents > 100_000_00) return { cents: null, error: 'That is more than $100,000' };
	return { cents };
}

/** Cents → what the dollar field shows: "612", or "612.50" when there are cents. */
export function centsToDollars(cents: number | null | undefined): string {
	if (cents === null || cents === undefined) return '';
	return cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2);
}

function realDate(s: string): boolean {
	if (!ISO.test(s)) return false;
	const d = new Date(`${s}T00:00:00Z`);
	return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

/** Today in the browser's time zone, as YYYY-MM-DD. */
export function todayIso(now: Date = new Date()): string {
	const y = now.getFullYear();
	const m = String(now.getMonth() + 1).padStart(2, '0');
	const d = String(now.getDate()).padStart(2, '0');
	return `${y}-${m}-${d}`;
}

function stopsValue(s: string): number | null {
	return s === '' ? null : Number(s);
}

function name(errors: Errors, value: string): void {
	if (!value.trim()) errors.name = 'Give it a name';
	else if (value.trim().length > 80) errors.name = 'Keep the name under 80 characters';
}

function code(errors: Errors, field: string, value: string, what: string): void {
	if (!value) errors[field] = `Choose ${what}`;
	else if (!CODE.test(value)) errors[field] = `${value} is not a three-letter airport code`;
}

function money(errors: Errors, field: string, text: string): number | null {
	const { cents, error } = parseDollars(text);
	if (error) errors[field] = error;
	return cents;
}

// -- trips -------------------------------------------------------------------------------

export interface TripDraft {
	name: string;
	origin: string;
	destination: string;
	depart_date: string;
	return_date: string;
	oneWay: boolean;
	target: string; // dollars
	max_stops: string; // '' = any
	notes: string;
}

export function tripErrors(d: TripDraft, today: string = todayIso()): Errors {
	const e: Errors = {};
	name(e, d.name);
	code(e, 'origin', d.origin, 'where you fly from');
	code(e, 'destination', d.destination, 'where you fly to');
	if (!e.origin && !e.destination && d.origin === d.destination)
		e.destination = 'The destination is the same as the origin';
	if (!d.depart_date) e.depart_date = 'Choose the departure date';
	else if (!realDate(d.depart_date)) e.depart_date = 'That is not a date';
	else if (d.depart_date < today) e.depart_date = 'That date has passed';
	if (!d.oneWay) {
		if (!d.return_date) e.return_date = 'Choose the return date, or make it one way';
		else if (!realDate(d.return_date)) e.return_date = 'That is not a date';
		else if (!e.depart_date && d.return_date <= d.depart_date)
			e.return_date = 'The return must be after the departure';
	}
	money(e, 'target', d.target);
	if (d.notes.length > 2000) e.notes = 'Keep the notes under 2,000 characters';
	return e;
}

export function toTripIn(d: TripDraft, status: TripIn['status'] = 'active'): TripIn {
	return {
		name: d.name.trim(),
		origin: d.origin,
		destination: d.destination,
		depart_date: d.depart_date,
		return_date: d.oneWay ? null : d.return_date,
		target_price: parseDollars(d.target).cents,
		max_stops: stopsValue(d.max_stops),
		notes: d.notes.trim() || null,
		status
	};
}

export function tripDraft(t?: Partial<TripIn>, home = ''): TripDraft {
	return {
		name: t?.name ?? '',
		origin: t?.origin ?? home,
		destination: t?.destination ?? '',
		depart_date: t?.depart_date ?? '',
		return_date: t?.return_date ?? '',
		oneWay: t ? !t.return_date : false,
		target: centsToDollars(t?.target_price),
		max_stops: t?.max_stops === null || t?.max_stops === undefined ? '' : String(t.max_stops),
		notes: t?.notes ?? ''
	};
}

// -- flexible searches -------------------------------------------------------------------

export interface SearchDraft {
	name: string;
	origin: string;
	destinations: string[];
	dows: Dow[];
	nights: string;
	rolling: boolean;
	window_start: string;
	window_end: string;
	target: string;
	max_stops: string;
}

export function searchErrors(d: SearchDraft, today: string = todayIso()): Errors {
	const e: Errors = {};
	name(e, d.name);
	code(e, 'origin', d.origin, 'where you fly from');
	if (!d.destinations.length) e.destinations = 'Add at least one destination';
	else if (d.destinations.length > 10) e.destinations = 'At most 10 destinations';
	else if (d.destinations.some((c) => !CODE.test(c)))
		e.destinations = 'Use three-letter airport codes';
	else if (d.origin && d.destinations.includes(d.origin))
		e.destinations = `${d.origin} is both the origin and a destination`;
	if (!d.dows.length) e.dows = 'Choose at least one weekday';
	const n = d.nights.trim();
	if (!/^\d+$/.test(n) || Number(n) > 60) e.nights = 'Nights: a whole number from 0 to 60';
	if (!d.rolling) {
		if (!d.window_start) e.window_start = 'Choose the first departure date';
		else if (!realDate(d.window_start)) e.window_start = 'That is not a date';
		if (!d.window_end) e.window_end = 'Choose the last departure date';
		else if (!realDate(d.window_end)) e.window_end = 'That is not a date';
		else if (d.window_end < today) e.window_end = 'That date has passed';
		else if (!e.window_start && d.window_end < d.window_start)
			e.window_end = 'The window ends before it starts';
	}
	money(e, 'target', d.target);
	return e;
}

export function toSearchIn(d: SearchDraft, status: SearchIn['status'] = 'active'): SearchIn {
	return {
		name: d.name.trim(),
		origin: d.origin,
		destinations: d.destinations as SearchIn['destinations'],
		depart_dows: DOWS.filter((x) => d.dows.includes(x)) as unknown as SearchIn['depart_dows'],
		nights: Number(d.nights),
		window_start: d.rolling ? null : d.window_start,
		window_end: d.rolling ? null : d.window_end,
		target_price: parseDollars(d.target).cents,
		max_stops: stopsValue(d.max_stops),
		status
	};
}

export function searchDraft(s?: Partial<SearchIn>, home = ''): SearchDraft {
	return {
		name: s?.name ?? '',
		origin: s?.origin ?? home,
		destinations: [...(s?.destinations ?? [])],
		dows: [...((s?.depart_dows ?? []) as Dow[])],
		nights: s?.nights === undefined ? '2' : String(s.nights),
		rolling: s ? !s.window_start && !s.window_end : true,
		window_start: s?.window_start ?? '',
		window_end: s?.window_end ?? '',
		target: centsToDollars(s?.target_price),
		max_stops: s?.max_stops === null || s?.max_stops === undefined ? '' : String(s.max_stops)
	};
}

// -- deal watches ------------------------------------------------------------------------

export interface DealWatchDraft {
	name: string;
	origins: string; // comma-separated words
	destinations: string;
	region: string; // '' = none
	max: string; // dollars
}

/** "Seattle, SEA,  west coast" → ["Seattle", "SEA", "west coast"]. */
export function words(text: string): string[] {
	return [
		...new Set(
			text
				.split(',')
				.map((w) => w.trim())
				.filter(Boolean)
		)
	];
}

export function dealWatchErrors(d: DealWatchDraft): Errors {
	const e: Errors = {};
	name(e, d.name);
	if (words(d.origins).length > 20) e.origins = 'At most 20 words';
	if (words(d.destinations).length + (d.region ? 1 : 0) > 20) e.destinations = 'At most 20 words';
	if (!words(d.origins).length && !words(d.destinations).length && !d.region)
		e.origins = 'Give origin words, destination words or a region, or it matches every deal';
	money(e, 'max', d.max);
	return e;
}

export function toDealWatchIn(
	d: DealWatchDraft,
	status: DealWatchIn['status'] = 'active'
): DealWatchIn {
	const dests = words(d.destinations).filter((w) => w.toLowerCase() !== d.region);
	return {
		name: d.name.trim(),
		origins: words(d.origins) as DealWatchIn['origins'],
		destinations: (d.region ? [d.region, ...dests] : dests) as DealWatchIn['destinations'],
		max_price: parseDollars(d.max).cents,
		status
	};
}

/** A stored watch back into the form: a region name in its destinations fills the menu. */
export function dealWatchDraft(w?: Partial<DealWatchIn>, regions: string[] = []): DealWatchDraft {
	const dests = [...(w?.destinations ?? [])];
	const region = dests.find((x) => regions.includes(x.toLowerCase()))?.toLowerCase() ?? '';
	return {
		name: w?.name ?? '',
		origins: (w?.origins ?? []).join(', '),
		destinations: dests.filter((x) => x.toLowerCase() !== region).join(', '),
		region,
		max: centsToDollars(w?.max_price)
	};
}

// -- deal feeds --------------------------------------------------------------------------

export interface FeedDraft {
	url: string;
	name: string;
}

export function feedErrors(d: FeedDraft): Errors {
	const e: Errors = {};
	const url = d.url.trim();
	if (!url) e.url = 'Paste the feed’s address';
	else if (!/^https?:\/\/[^\s/]+\.[^\s]+$/.test(url) || url.length < 10)
		e.url = 'That is not a web address (it starts with https://)';
	else if (url.length > 500) e.url = 'That address is too long';
	if (d.name.length > 80) e.name = 'Keep the name under 80 characters';
	return e;
}

export function toFeedIn(d: FeedDraft, status: DealFeedIn['status'] = 'active'): DealFeedIn {
	return { url: d.url.trim(), name: d.name.trim() || null, status };
}

// -- alert settings ----------------------------------------------------------------------

export interface SettingsDraft {
	dedup_days: string;
	dedup_further_drop_pct: string;
	percentile_alerts: boolean;
}

export function settingsErrors(d: SettingsDraft): Errors {
	const e: Errors = {};
	const days = d.dedup_days.trim();
	if (!/^\d+$/.test(days) || Number(days) < 1 || Number(days) > 60)
		e.dedup_days = 'A whole number of days from 1 to 60';
	const pct = d.dedup_further_drop_pct.trim();
	if (!/^\d+(\.\d+)?$/.test(pct) || Number(pct) > 90) e.dedup_further_drop_pct = 'From 0 to 90';
	return e;
}

export function toSettings(d: SettingsDraft): AlertSettings {
	return {
		dedup_days: Number(d.dedup_days),
		dedup_further_drop_pct: Number(d.dedup_further_drop_pct),
		percentile_alerts: d.percentile_alerts
	};
}

export const settingsDraft = (s: AlertSettings): SettingsDraft => ({
	dedup_days: String(s.dedup_days),
	dedup_further_drop_pct: String(s.dedup_further_drop_pct),
	percentile_alerts: s.percentile_alerts
});

export const valid = (e: Errors) => Object.keys(e).length === 0;
