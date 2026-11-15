// Words and numbers the way the pages show them. Prices arrive as integer cents,
// instants as ISO 8601 UTC, calendar dates as YYYY-MM-DD (no time zone).

const WEEKDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DOW_NAMES: Record<string, string> = {
	MON: 'Mondays',
	TUE: 'Tuesdays',
	WED: 'Wednesdays',
	THU: 'Thursdays',
	FRI: 'Fridays',
	SAT: 'Saturdays',
	SUN: 'Sundays'
};

const moneyFormats = new Map<string, Intl.NumberFormat>();

/** "$1,036" from 103600 cents; "–" for nothing. Whole units: fares are never quoted in cents. */
export function money(cents: number | null | undefined, currency = 'USD'): string {
	if (cents === null || cents === undefined || !Number.isFinite(cents)) return '–';
	let f = moneyFormats.get(currency);
	if (!f) {
		f = new Intl.NumberFormat('en-US', {
			style: 'currency',
			currency,
			maximumFractionDigits: 0,
			minimumFractionDigits: 0
		});
		moneyFormats.set(currency, f);
	}
	return f.format(Math.round(cents / 100));
}

function parts(date: string): [number, number, number] {
	const [y, m, d] = date.split('-').map(Number);
	return [y, m, d];
}

/** "Fri" for a YYYY-MM-DD date. */
export function weekday(date: string): string {
	const [y, m, d] = parts(date);
	return WEEKDAY[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
}

/** "Apr 17" */
export function monthDay(date: string): string {
	const [, m, d] = parts(date);
	return `${MONTH[m - 1]} ${d}`;
}

/** "Sat Apr 17" */
export function dayDate(date: string): string {
	return `${weekday(date)} ${monthDay(date)}`;
}

/** "Sat Apr 17 – Sat May 1", or "Sat Apr 17, one way". */
export function tripDates(depart: string, ret: string | null | undefined): string {
	return ret ? `${dayDate(depart)} – ${dayDate(ret)}` : `${dayDate(depart)}, one way`;
}

/** "April 2027" for the first day of a month. */
export function monthTitle(date: string): string {
	const [y, m] = parts(date);
	return `${new Date(Date.UTC(y, m - 1, 1)).toLocaleString('en-US', { month: 'long', timeZone: 'UTC' })} ${y}`;
}

/** Days between two calendar dates (b − a). */
export function daysBetween(a: string, b: string): number {
	const [y1, m1, d1] = parts(a);
	const [y2, m2, d2] = parts(b);
	return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86_400_000);
}

/** "5 min ago", "3 h ago", "2 days ago" for an instant (never "in the future"). */
export function ago(iso: string | null | undefined, now: number = Date.now()): string {
	if (!iso) return 'never';
	const m = Math.max(0, Math.round((now - Date.parse(iso)) / 60000));
	if (m < 1) return 'just now';
	if (m < 60) return `${m} min ago`;
	const h = Math.round(m / 60);
	return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

/** "in 25 min", "in 3 h" for an instant ahead; "now" once it has passed. */
export function until(iso: string | null | undefined, now: number = Date.now()): string {
	if (!iso) return '–';
	const m = Math.round((Date.parse(iso) - now) / 60000);
	if (m <= 0) return 'now';
	if (m < 60) return `in ${m} min`;
	const h = Math.round(m / 60);
	return h < 48 ? `in ${h} h` : `in ${Math.round(h / 24)} days`;
}

/** "Nov 15, 2:00 PM PST" for an instant, in the browser's time zone (named). */
export function instant(iso: string | null | undefined): string {
	if (!iso) return '–';
	return new Date(iso).toLocaleString('en-US', {
		month: 'short',
		day: 'numeric',
		hour: 'numeric',
		minute: '2-digit',
		timeZoneName: 'short'
	});
}

/** "Nov 15" for an instant, in the browser's time zone. */
export function instantDay(iso: string): string {
	return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/** "10 h 40 min", "55 min". */
export function duration(min: number | null | undefined): string {
	if (min === null || min === undefined) return '–';
	const h = Math.floor(min / 60);
	const m = min % 60;
	if (!h) return `${m} min`;
	return m ? `${h} h ${m} min` : `${h} h`;
}

/** "Nonstop", "1 stop", "2 stops". */
export function stops(n: number | null | undefined): string {
	if (n === null || n === undefined) return '–';
	if (n === 0) return 'Nonstop';
	return n === 1 ? '1 stop' : `${n} stops`;
}

/** "6:05 AM" from a local airport time "YYYY-MM-DD HH:MM"; with "+1" when it lands on a
 * later day than `from`. */
export function clock(local: string | null | undefined, from?: string | null): string {
	if (!local) return '–';
	const [date, time] = local.split(' ');
	if (!time) return local;
	const [hh, mm] = time.split(':').map(Number);
	const h12 = hh % 12 === 0 ? 12 : hh % 12;
	const label = `${h12}:${String(mm).padStart(2, '0')} ${hh < 12 ? 'AM' : 'PM'}`;
	if (from) {
		const later = daysBetween(from.split(' ')[0], date);
		if (later > 0) return `${label} +${later}`;
	}
	return label;
}

/** The week's change: "↓ 9%", "↑ 3%", "→ 0%". */
export function change(pct: number | null | undefined): {
	text: string;
	dir: 'down' | 'up' | 'flat';
} {
	if (pct === null || pct === undefined) return { text: '–', dir: 'flat' };
	const r = Math.round(pct);
	if (r <= -1) return { text: `↓ ${Math.abs(r)}%`, dir: 'down' };
	if (r >= 1) return { text: `↑ ${r}%`, dir: 'up' };
	return { text: '→ 0%', dir: 'flat' };
}

/** "2.1 MB" */
export function bytes(n: number): string {
	if (n < 1024) return `${n} B`;
	if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
	if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
	return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

/** "Fridays", "Thursdays and Fridays". */
export function weekdays(dows: string[]): string {
	const names = dows.map((d) => DOW_NAMES[d.toUpperCase()] ?? d);
	if (names.length <= 1) return names[0] ?? '';
	return `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
}

/** "3 nights", "1 night", "one way". */
export function nights(n: number): string {
	if (n === 0) return 'one way';
	return n === 1 ? '1 night' : `${n} nights`;
}

/** Why an alert was sent, in words. */
export function alertReason(reason: string): string {
	return (
		{
			threshold: 'under your target',
			percentile: 'unusually cheap for this route',
			all_time_low: 'the lowest price seen'
		}[reason] ?? reason.replaceAll('_', ' ')
	);
}

/** The words for a request outcome on the status page. */
export function outcomeLabel(outcome: string): string {
	return (
		{
			ok: 'ok',
			empty: 'no flights',
			blocked: 'blocked',
			network: 'network error',
			layout: 'unreadable page',
			unknown: 'other error'
		}[outcome] ?? outcome.replaceAll('_', ' ')
	);
}
