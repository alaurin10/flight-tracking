import { describe, expect, it } from 'vitest';
import { flagWords, months, priceBins } from './heat';
import type { DatePairOut, SearchOut } from './types.gen';
import search from './fixtures/search.json';

const pair = (id: number, date: string, price: number | null): DatePairOut =>
	({ query_id: id, depart_date: date, price, flags: [] }) as unknown as DatePairOut;

describe('price bins', () => {
	it('puts the cheapest quarter in bin 1 and the dearest in bin 4', () => {
		const pairs = [100, 200, 300, 400, 500, 600, 700, 800].map((p, i) =>
			pair(i, `2027-01-0${i + 1}`, p)
		);
		const bins = priceBins(pairs);
		expect([...bins.values()]).toEqual([1, 1, 2, 2, 3, 3, 4, 4]);
	});

	it('gives equal prices the same bin and leaves unpriced dates out', () => {
		const bins = priceBins([
			pair(1, '2027-01-01', 100),
			pair(2, '2027-01-02', 100),
			pair(3, '2027-01-03', 900),
			pair(4, '2027-01-04', null)
		]);
		expect(bins.get(1)).toBe(bins.get(2));
		expect(bins.get(3)).toBeGreaterThan(bins.get(1)!);
		expect(bins.has(4)).toBe(false);
		expect(priceBins([pair(1, '2027-01-01', 5)]).get(1)).toBe(1);
	});
});

describe('months', () => {
	it('lays out every month from the first departure to the last, Monday first', () => {
		const m = months([pair(1, '2026-12-11', 1), pair(2, '2027-02-05', 2)]);
		expect(m.map((x) => x.first)).toEqual(['2026-12-01', '2027-01-01', '2027-02-01']);
		expect(m[0].lead).toBe(1); // 1 Dec 2026 is a Tuesday
		expect(m[0].days).toHaveLength(31);
		expect(m[0].days[10].pair?.query_id).toBe(1);
		expect(m[1].days.every((d) => d.pair === null)).toBe(true);
		expect(m[2].days).toHaveLength(28);
		expect(months([])).toEqual([]);
	});

	it('places every pair of a real search exactly once', () => {
		const pairs = (search as unknown as SearchOut).grid[0].pairs;
		const placed = months(pairs).flatMap((m) => m.days.filter((d) => d.pair));
		expect(placed).toHaveLength(pairs.length);
	});

	it('words the flags', () => {
		expect(flagWords(['cheapest', 'under_target', 'estimate'])).toEqual([
			'cheapest',
			'under target',
			'calendar estimate'
		]);
	});
});
