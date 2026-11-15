// The date grids' arithmetic: price bins and month layouts. Pure, so it is unit tested.
import type { DatePairOut } from './types.gen';

/** Price bins 1 (cheapest quarter) to 4 (dearest), by rank, so one expensive date doesn't
 * wash out the rest. Unpriced pairs are left out. Equal prices share a bin. */
export function priceBins(pairs: DatePairOut[]): Map<number, number> {
	const priced = pairs.filter((p) => p.price !== null).sort((a, b) => a.price! - b.price!);
	const out = new Map<number, number>();
	const n = priced.length;
	let rank = 0;
	priced.forEach((p, i) => {
		if (i > 0 && p.price !== priced[i - 1].price) rank = i;
		out.set(p.query_id, n <= 1 ? 1 : Math.min(4, 1 + Math.floor((rank * 4) / n)));
	});
	return out;
}

export interface MonthCell {
	date: string;
	day: number;
	pair: DatePairOut | null;
}

export interface Month {
	/** YYYY-MM-01 */
	first: string;
	/** Blank cells before the 1st, Monday first. */
	lead: number;
	days: MonthCell[];
}

const iso = (y: number, m: number, d: number) =>
	`${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;

/** Every month from the first departure to the last, with each day's pair (if any). */
export function months(pairs: DatePairOut[]): Month[] {
	if (!pairs.length) return [];
	const byDate = new Map(pairs.map((p) => [p.depart_date, p]));
	const dates = [...byDate.keys()].sort();
	let [y, m] = dates[0].split('-').map(Number);
	const [y2, m2] = dates[dates.length - 1].split('-').map(Number);
	const out: Month[] = [];
	while (y < y2 || (y === y2 && m <= m2)) {
		const lead = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7;
		const count = new Date(Date.UTC(y, m, 0)).getUTCDate();
		const days: MonthCell[] = [];
		for (let d = 1; d <= count; d++) {
			const date = iso(y, m, d);
			days.push({ date, day: d, pair: byDate.get(date) ?? null });
		}
		out.push({ first: iso(y, m, 1), lead, days });
		m += 1;
		if (m > 12) {
			m = 1;
			y += 1;
		}
	}
	return out;
}

/** The flags in words, for a cell's label and the table. */
export function flagWords(flags: string[]): string[] {
	const words: Record<string, string> = {
		cheapest: 'cheapest',
		under_target: 'under target',
		at_low: 'at its 30-day low',
		estimate: 'calendar estimate'
	};
	return flags.map((f) => words[f] ?? f.replaceAll('_', ' '));
}
