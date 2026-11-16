// What the Manage forms check before anything is sent, and how a form's fields become the
// API's shapes. The forms follow the four ways of watching:
//
//   a trip           a destination on set dates: the cheapest flight       → trip
//   flexible dates   a destination, any weekend in May (say)               → flexible search
//   a region         anywhere in Europe in the summer, from deal pages     → deal watch
//   a deal page      a site's page or feed, read for posts                 → deal source
//
// Only what matters is required; a name is made up when none is given. The server checks
// everything again (airport codes against its list, the limits).
//
// Money is typed in dollars ("612", "$1,250", "612.50") and sent in cents.
import type { AlertSettings, DealFeedIn, DealWatchIn, SearchIn, TripIn } from '../types.gen';

export type Errors = Record<string, string>;

export const DOWS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'] as const;
export type Dow = (typeof DOWS)[number];
export const MONTHS = [
	'Jan',
	'Feb',
	'Mar',
	'Apr',
	'May',
	'Jun',
	'Jul',
	'Aug',
	'Sep',
	'Oct',
	'Nov',
	'Dec'
] as const;
const MONTH_NAMES = [
	'January',
	'February',
	'March',
	'April',
	'May',
	'June',
	'July',
	'August',
	'September',
	'October',
	'November',
	'December'
];

const CODE = /^[A-Z]{3}$/;
const ISO = /^\d{4}-\d{2}-\d{2}$/;
const YM = /^\d{4}-\d{2}$/;

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

function checkName(errors: Errors, value: string): void {
	if (value.trim().length > 80) errors.name = 'Keep the name under 80 characters';
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

// -- months --------------------------------------------------------------------------

/** "2027-05" → "May 2027". */
export function monthLabel(ym: string): string {
	const [y, m] = ym.split('-').map(Number);
	return `${MONTH_NAMES[m - 1]} ${y}`;
}

/** The next `count` months from today's, as YYYY-MM, for the month menus. */
export function monthOptions(today: string = todayIso(), count = 18): string[] {
	let [y, m] = today.split('-').map(Number);
	const out: string[] = [];
	for (let i = 0; i < count; i++) {
		out.push(`${y}-${String(m).padStart(2, '0')}`);
		[y, m] = m === 12 ? [y + 1, 1] : [y, m + 1];
	}
	return out;
}

/** The last day of a YYYY-MM month, as YYYY-MM-DD. */
export function monthEnd(ym: string): string {
	const [y, m] = ym.split('-').map(Number);
	const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
	return `${ym}-${String(last).padStart(2, '0')}`;
}

/** Month numbers (1-12) as a short phrase: "Jun–Aug", "Dec–Feb", "Mar, May". */
export function monthsPhrase(months: number[]): string {
	const set = [...new Set(months)].filter((m) => m >= 1 && m <= 12).sort((a, b) => a - b);
	if (!set.length || set.length === 12) return 'any time';
	// Runs, allowing one to wrap over the new year (Dec–Feb).
	const runs: number[][] = [];
	for (const m of set) {
		const last = runs.at(-1);
		if (last && m === last.at(-1)! + 1) last.push(m);
		else runs.push([m]);
	}
	if (runs.length > 1 && runs[0][0] === 1 && runs.at(-1)!.at(-1) === 12) {
		runs[0] = [...runs.pop()!, ...runs[0]];
	}
	return runs
		.map((r) =>
			r.length === 1 ? MONTHS[r[0] - 1] : `${MONTHS[r[0] - 1]}–${MONTHS[r.at(-1)! - 1]}`
		)
		.join(', ');
}

export const SEASONS: { label: string; months: number[] }[] = [
	{ label: 'Spring', months: [3, 4, 5] },
	{ label: 'Summer', months: [6, 7, 8] },
	{ label: 'Fall', months: [9, 10, 11] },
	{ label: 'Winter', months: [12, 1, 2] }
];

// -- trips ---------------------------------------------------------------------------

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
	checkName(e, d.name);
	code(e, 'destination', d.destination, 'where you want to go');
	code(e, 'origin', d.origin, 'where you fly from');
	if (!e.origin && !e.destination && d.origin === d.destination)
		e.destination = 'That is where you fly from';
	if (!d.depart_date) e.depart_date = 'Choose the day you leave';
	else if (!realDate(d.depart_date)) e.depart_date = 'That is not a date';
	else if (d.depart_date < today) e.depart_date = 'That date has passed';
	if (!d.oneWay) {
		if (!d.return_date) e.return_date = 'Choose the day you come back, or make it one way';
		else if (!realDate(d.return_date)) e.return_date = 'That is not a date';
		else if (!e.depart_date && d.return_date <= d.depart_date)
			e.return_date = 'Coming back must be after leaving';
	}
	money(e, 'target', d.target);
	if (d.notes.length > 2000) e.notes = 'Keep the notes under 2,000 characters';
	return e;
}

/** "Tokyo · Apr 10" when no name is given. */
export function tripName(d: TripDraft, place: (code: string) => string = (c) => c): string {
	if (d.name.trim()) return d.name.trim();
	if (!d.destination) return '';
	const day = d.depart_date && realDate(d.depart_date) ? d.depart_date : '';
	if (!day) return place(d.destination);
	const [, m, dd] = day.split('-').map(Number);
	return `${place(d.destination)} · ${MONTHS[m - 1]} ${dd}`;
}

export function toTripIn(
	d: TripDraft,
	status: TripIn['status'] = 'active',
	place?: (code: string) => string
): TripIn {
	return {
		name: tripName(d, place),
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

// -- flexible dates ------------------------------------------------------------------

/** How long a trip is: the presets cover most, Custom covers the rest. */
export const LENGTHS = {
	weekend: { label: 'Weekend', hint: 'Fri – Sun', dows: ['FRI'] as Dow[], nights: 2 },
	long: {
		label: 'Long weekend',
		hint: 'Thu or Fri, 3 nights',
		dows: ['THU', 'FRI'] as Dow[],
		nights: 3
	},
	week: { label: 'A week', hint: 'Sat – Sat', dows: ['SAT'] as Dow[], nights: 7 }
} as const;
export type Length = keyof typeof LENGTHS | 'custom';

export type When = 'any' | 'months' | 'dates';

export interface SearchDraft {
	name: string;
	origin: string;
	destinations: string[];
	length: Length;
	dows: Dow[]; // custom
	nights: string; // custom
	when: When;
	month_from: string; // YYYY-MM
	month_to: string;
	window_start: string; // dates
	window_end: string;
	target: string;
	max_stops: string;
}

function lengthOf(dows: string[], nights: number): Length {
	const same = (a: readonly string[]) =>
		a.length === dows.length && a.every((x) => dows.includes(x));
	for (const [k, v] of Object.entries(LENGTHS))
		if (same(v.dows) && v.nights === nights) return k as Length;
	return 'custom';
}

/** The weekdays and nights a draft stands for. */
export function shapeOf(d: SearchDraft): { dows: Dow[]; nights: number } {
	if (d.length === 'custom')
		return { dows: DOWS.filter((x) => d.dows.includes(x)), nights: Number(d.nights) };
	const p = LENGTHS[d.length];
	return { dows: [...p.dows], nights: p.nights };
}

/** The departure window a draft stands for (both null = any time). */
export function windowOf(d: SearchDraft): { start: string | null; end: string | null } {
	if (d.when === 'months') return { start: `${d.month_from}-01`, end: monthEnd(d.month_to) };
	if (d.when === 'dates') return { start: d.window_start, end: d.window_end };
	return { start: null, end: null };
}

export function searchErrors(d: SearchDraft, today: string = todayIso()): Errors {
	const e: Errors = {};
	checkName(e, d.name);
	code(e, 'origin', d.origin, 'where you fly from');
	if (!d.destinations.length) e.destinations = 'Add where you want to go';
	else if (d.destinations.length > 10) e.destinations = 'At most 10 destinations';
	else if (d.destinations.some((c) => !CODE.test(c)))
		e.destinations = 'Use three-letter airport codes';
	else if (d.origin && d.destinations.includes(d.origin))
		e.destinations = `${d.origin} is where you fly from`;
	if (d.length === 'custom') {
		if (!d.dows.length) e.dows = 'Choose at least one day to leave on';
		const n = d.nights.trim();
		if (!/^\d+$/.test(n) || Number(n) > 60) e.nights = 'Nights: a whole number from 0 to 60';
	}
	if (d.when === 'months') {
		if (!YM.test(d.month_from)) e.month_from = 'Choose a month';
		if (!YM.test(d.month_to)) e.month_to = 'Choose a month';
		else if (!e.month_from && d.month_to < d.month_from)
			e.month_to = 'This is before the first month';
		else if (monthEnd(d.month_to) < today) e.month_to = 'That month has passed';
	} else if (d.when === 'dates') {
		if (!d.window_start) e.window_start = 'Choose the first day you could leave';
		else if (!realDate(d.window_start)) e.window_start = 'That is not a date';
		if (!d.window_end) e.window_end = 'Choose the last day you could leave';
		else if (!realDate(d.window_end)) e.window_end = 'That is not a date';
		else if (d.window_end < today) e.window_end = 'That date has passed';
		else if (!e.window_start && d.window_end < d.window_start)
			e.window_end = 'This is before the first day';
	}
	money(e, 'target', d.target);
	return e;
}

/** "Salt Lake City weekends · May 2027" when no name is given. */
export function searchName(d: SearchDraft, place: (code: string) => string = (c) => c): string {
	if (d.name.trim()) return d.name.trim();
	if (!d.destinations.length) return '';
	const where =
		d.destinations.length <= 2
			? d.destinations.map(place).join(' & ')
			: `${place(d.destinations[0])} & ${d.destinations.length - 1} more`;
	const how =
		d.length === 'weekend'
			? 'weekends'
			: d.length === 'long'
				? 'long weekends'
				: d.length === 'week'
					? 'weeks'
					: 'trips';
	let when = '';
	if (d.when === 'months' && YM.test(d.month_from) && YM.test(d.month_to)) {
		const [y1, m1] = d.month_from.split('-').map(Number);
		const [y2, m2] = d.month_to.split('-').map(Number);
		when =
			d.month_from === d.month_to
				? `${MONTHS[m1 - 1]} ${y1}`
				: y1 === y2
					? `${MONTHS[m1 - 1]}–${MONTHS[m2 - 1]} ${y2}`
					: `${MONTHS[m1 - 1]} ${y1} – ${MONTHS[m2 - 1]} ${y2}`;
	}
	return `${where} ${how}${when ? ` · ${when}` : ''}`.slice(0, 80);
}

export function toSearchIn(
	d: SearchDraft,
	status: SearchIn['status'] = 'active',
	place?: (code: string) => string
): SearchIn {
	const { dows, nights } = shapeOf(d);
	const w = windowOf(d);
	return {
		name: searchName(d, place),
		origin: d.origin,
		destinations: d.destinations as SearchIn['destinations'],
		depart_dows: dows as unknown as SearchIn['depart_dows'],
		nights,
		window_start: w.start,
		window_end: w.end,
		target_price: parseDollars(d.target).cents,
		max_stops: stopsValue(d.max_stops),
		status
	};
}

export function searchDraft(
	s?: Partial<SearchIn>,
	home = '',
	today: string = todayIso()
): SearchDraft {
	const next = monthOptions(today, 2)[1];
	const dows = [...((s?.depart_dows ?? []) as Dow[])];
	const nights = s?.nights ?? 2;
	const start = s?.window_start ?? '';
	const end = s?.window_end ?? '';
	// A window from a month's first day to a month's last is shown as months.
	const monthly = !!start && !!end && start.endsWith('-01') && monthEnd(end.slice(0, 7)) === end;
	return {
		name: s?.name ?? '',
		origin: s?.origin ?? home,
		destinations: [...(s?.destinations ?? [])],
		length: s ? lengthOf(dows, nights) : 'weekend',
		dows: dows.length ? dows : ['FRI'],
		nights: String(nights),
		when: !s ? 'months' : !start && !end ? 'any' : monthly ? 'months' : 'dates',
		month_from: monthly ? start.slice(0, 7) : next,
		month_to: monthly ? end.slice(0, 7) : next,
		window_start: start,
		window_end: end,
		target: centsToDollars(s?.target_price),
		max_stops: s?.max_stops === null || s?.max_stops === undefined ? '' : String(s.max_stops)
	};
}

// -- regions (deal watches) ----------------------------------------------------------

export interface RegionDraft {
	name: string;
	region: string; // '' = none
	places: string; // comma-separated words
	months: number[]; // [] = any time
	max: string; // dollars
	origins: string; // comma-separated words
	keywords: string[]; // kept from an imported watch; not edited here
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

const title = (r: string) => r.replace(/\b\w/g, (c) => c.toUpperCase());

export function regionErrors(d: RegionDraft): Errors {
	const e: Errors = {};
	checkName(e, d.name);
	if (!d.region && !words(d.places).length) e.region = 'Choose a region, or name some places';
	if (words(d.places).length + (d.region ? 1 : 0) > 20) e.places = 'At most 20 places';
	if (words(d.origins).length > 20) e.origins = 'At most 20 words';
	money(e, 'max', d.max);
	return e;
}

/** "Europe · Jun–Aug" when no name is given. */
export function regionName(d: RegionDraft): string {
	if (d.name.trim()) return d.name.trim();
	const p = words(d.places);
	const where = d.region
		? title(d.region)
		: p.length > 2
			? `${p[0]} & ${p.length - 1} more`
			: p.join(' & ');
	if (!where) return '';
	return d.months.length ? `${where} · ${monthsPhrase(d.months)}` : where;
}

export function toDealWatchIn(
	d: RegionDraft,
	status: DealWatchIn['status'] = 'active'
): DealWatchIn {
	const places = words(d.places).filter((w) => w.toLowerCase() !== d.region);
	return {
		name: regionName(d),
		origins: words(d.origins) as DealWatchIn['origins'],
		destinations: (d.region ? [d.region, ...places] : places) as DealWatchIn['destinations'],
		keywords: d.keywords as DealWatchIn['keywords'],
		months: [...d.months].sort((a, b) => a - b) as DealWatchIn['months'],
		max_price: parseDollars(d.max).cents,
		status
	};
}

/** A stored watch back into the form: a region name in its destinations fills the menu. */
export function regionDraft(
	w?: Partial<DealWatchIn>,
	regions: string[] = [],
	homeWords = ''
): RegionDraft {
	const dests = [...(w?.destinations ?? [])];
	const region = dests.find((x) => regions.includes(x.toLowerCase()))?.toLowerCase() ?? '';
	return {
		name: w?.name ?? '',
		region,
		places: dests.filter((x) => x.toLowerCase() !== region).join(', '),
		months: [...(w?.months ?? [])],
		max: centsToDollars(w?.max_price),
		origins: w ? (w.origins ?? []).join(', ') : homeWords,
		keywords: [...(w?.keywords ?? [])]
	};
}

// -- deal pages ----------------------------------------------------------------------

export interface FeedDraft {
	url: string;
	name: string;
}

/** What was typed, as an address: "escape.flights/x" → "https://escape.flights/x". */
export function asUrl(text: string): string {
	const t = text.trim();
	if (!t) return '';
	return /^https?:\/\//i.test(t) ? t : `https://${t}`;
}

export function feedErrors(d: FeedDraft): Errors {
	const e: Errors = {};
	const url = asUrl(d.url);
	if (!url) e.url = 'Paste the page’s address';
	else if (!/^https?:\/\/[^\s/]+\.[^\s]+$/.test(url) || url.length < 10)
		e.url = 'That is not a web address';
	else if (url.length > 500) e.url = 'That address is too long';
	if (d.name.length > 80) e.name = 'Keep the name under 80 characters';
	return e;
}

export function toFeedIn(d: FeedDraft, status: DealFeedIn['status'] = 'active'): DealFeedIn {
	return { url: asUrl(d.url), name: d.name.trim() || null, status };
}

// -- alert settings ------------------------------------------------------------------

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
