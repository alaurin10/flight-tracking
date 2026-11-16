// The Manage forms' checks, the names they make up, and how their fields become the
// API's shapes.
import { describe, expect, it } from 'vitest';
import {
	asUrl,
	centsToDollars,
	feedErrors,
	monthEnd,
	monthOptions,
	monthsPhrase,
	parseDollars,
	regionDraft,
	regionErrors,
	regionName,
	searchDraft,
	searchErrors,
	searchName,
	settingsErrors,
	toDealWatchIn,
	toSearchIn,
	toTripIn,
	tripDraft,
	tripErrors,
	tripName,
	valid,
	words,
	type RegionDraft,
	type SearchDraft,
	type TripDraft
} from './validate';

const TODAY = '2027-01-10';
const city: Record<string, string> = { HND: 'Tokyo', SLC: 'Salt Lake City', DEN: 'Denver' };
const place = (c: string) => city[c] ?? c;

describe('money', () => {
	it('reads dollars as typed and sends cents', () => {
		expect(parseDollars('612')).toEqual({ cents: 61200 });
		expect(parseDollars(' $1,250 ')).toEqual({ cents: 125000 });
		expect(parseDollars('612.5')).toEqual({ cents: 61250 });
		expect(parseDollars('')).toEqual({ cents: null });
		for (const t of ['-5', 'abc', '12.345', '1e3', '200000'])
			expect(parseDollars(t).error).toBeTruthy();
	});

	it('shows cents only when there are some', () => {
		expect(centsToDollars(61200)).toBe('612');
		expect(centsToDollars(61250)).toBe('612.50');
		expect(centsToDollars(null)).toBe('');
	});
});

describe('months', () => {
	it('lists the coming months and knows their last days', () => {
		expect(monthOptions('2026-11-15', 4)).toEqual(['2026-11', '2026-12', '2027-01', '2027-02']);
		expect(monthEnd('2027-02')).toBe('2027-02-28');
		expect(monthEnd('2028-02')).toBe('2028-02-29');
	});

	it('says months as a short phrase, wrapping over the new year', () => {
		expect(monthsPhrase([6, 7, 8])).toBe('Jun–Aug');
		expect(monthsPhrase([12, 1, 2])).toBe('Dec–Feb');
		expect(monthsPhrase([3, 5])).toBe('Mar, May');
		expect(monthsPhrase([])).toBe('any time');
	});
});

const trip = (over: Partial<TripDraft> = {}): TripDraft => ({
	...tripDraft(undefined, 'SEA'),
	destination: 'HND',
	depart_date: '2027-04-10',
	return_date: '2027-04-24',
	...over
});

describe('a trip', () => {
	it('needs only where and when; the name is made up', () => {
		const d = trip();
		expect(tripErrors(d, TODAY)).toEqual({});
		expect(toTripIn(d, 'active', place)).toEqual({
			name: 'Tokyo · Apr 10',
			origin: 'SEA',
			destination: 'HND',
			depart_date: '2027-04-10',
			return_date: '2027-04-24',
			target_price: null,
			max_stops: null,
			notes: null,
			status: 'active'
		});
		expect(tripName(trip({ name: '  Cherry blossoms ' }))).toBe('Cherry blossoms');
	});

	it('says what is missing or wrong, field by field', () => {
		const e = tripErrors(
			trip({ destination: 'SEA', depart_date: '2027-01-09', target: 'lots' }),
			TODAY
		);
		expect(Object.keys(e).sort()).toEqual(['depart_date', 'destination', 'target']);
		expect(e.destination).toMatch(/where you fly from/);
		expect(e.depart_date).toMatch(/passed/);
		expect(tripErrors(trip({ destination: '' }), TODAY).destination).toMatch(/where you want/);
	});

	it('needs coming back after leaving, unless it is one way', () => {
		expect(tripErrors(trip({ return_date: '2027-04-10' }), TODAY).return_date).toMatch(/after/);
		expect(tripErrors(trip({ return_date: '' }), TODAY).return_date).toMatch(/one way/);
		const oneWay = trip({ oneWay: true, return_date: '2027-04-01' });
		expect(valid(tripErrors(oneWay, TODAY))).toBe(true);
		expect(toTripIn(oneWay).return_date).toBeNull();
	});

	it('round-trips a stored trip through the form', () => {
		const stored = toTripIn(trip({ name: 'Japan', target: '612.50', oneWay: true }), 'paused');
		expect(toTripIn(tripDraft(stored), 'paused')).toEqual(stored);
	});
});

const search = (over: Partial<SearchDraft> = {}): SearchDraft => ({
	...searchDraft(undefined, 'SEA', TODAY),
	destinations: ['SLC'],
	...over
});

describe('flexible dates', () => {
	it('starts as weekends next month, and that is enough to save', () => {
		const d = search();
		expect(d).toMatchObject({ length: 'weekend', when: 'months', month_from: '2027-02' });
		expect(searchErrors(d, TODAY)).toEqual({});
		expect(toSearchIn(d, 'active', place)).toMatchObject({
			name: 'Salt Lake City weekends · Feb 2027',
			destinations: ['SLC'],
			depart_dows: ['FRI'],
			nights: 2,
			window_start: '2027-02-01',
			window_end: '2027-02-28'
		});
	});

	it('turns each length into weekdays and nights', () => {
		expect(toSearchIn(search({ length: 'long' }))).toMatchObject({
			depart_dows: ['THU', 'FRI'],
			nights: 3
		});
		expect(toSearchIn(search({ length: 'week' }))).toMatchObject({
			depart_dows: ['SAT'],
			nights: 7
		});
		expect(
			toSearchIn(search({ length: 'custom', dows: ['SUN', 'WED'], nights: '4' }))
		).toMatchObject({ depart_dows: ['WED', 'SUN'], nights: 4 });
	});

	it('turns when into a window: months, any time or dates', () => {
		const may = search({
			month_from: '2027-05',
			month_to: '2027-06',
			destinations: ['SLC', 'DEN']
		});
		expect(toSearchIn(may, 'active', place)).toMatchObject({
			name: 'Salt Lake City & Denver weekends · May–Jun 2027',
			window_start: '2027-05-01',
			window_end: '2027-06-30'
		});
		expect(toSearchIn(search({ when: 'any' }))).toMatchObject({
			window_start: null,
			window_end: null
		});
		const dates = search({ when: 'dates', window_start: '2027-03-03', window_end: '2027-03-20' });
		expect(searchErrors(dates, TODAY)).toEqual({});
		expect(toSearchIn(dates)).toMatchObject({
			window_start: '2027-03-03',
			window_end: '2027-03-20'
		});
		expect(
			searchErrors(
				search({ when: 'dates', window_start: '2027-03-20', window_end: '2027-03-03' }),
				TODAY
			).window_end
		).toMatch(/before/);
	});

	it('needs a destination; custom needs days and sensible nights', () => {
		expect(searchErrors(search({ destinations: [] }), TODAY).destinations).toBeTruthy();
		const e = searchErrors(search({ length: 'custom', dows: [], nights: '2.5' }), TODAY);
		expect(Object.keys(e).sort()).toEqual(['dows', 'nights']);
		expect(searchErrors(search({ destinations: ['SEA'] }), TODAY).destinations).toMatch(
			/where you fly from/
		);
	});

	it('shows a stored search with the preset and months it matches', () => {
		const stored = toSearchIn(
			search({ length: 'long', month_from: '2027-05', month_to: '2027-05' })
		);
		expect(searchDraft(stored, 'SEA', TODAY)).toMatchObject({
			length: 'long',
			when: 'months',
			month_from: '2027-05',
			month_to: '2027-05'
		});
		const odd = searchDraft(
			{
				...stored,
				depart_dows: ['MON'],
				nights: 5,
				window_start: '2027-03-03',
				window_end: '2027-03-20'
			},
			'SEA',
			TODAY
		);
		expect(odd).toMatchObject({ length: 'custom', dows: ['MON'], nights: '5', when: 'dates' });
		expect(
			searchDraft({ ...stored, window_start: null, window_end: null }, 'SEA', TODAY).when
		).toBe('any');
		expect(searchName(search({ name: 'Mine' }))).toBe('Mine');
	});
});

const region = (over: Partial<RegionDraft> = {}): RegionDraft => ({
	...regionDraft(undefined, ['europe', 'hawaii'], 'Seattle, SEA'),
	region: 'europe',
	...over
});

describe('a region', () => {
	it('sends the region first, the months and home’s words', () => {
		const d = region({ places: 'Lisbon, Porto', months: [8, 6, 7], max: '600' });
		expect(regionErrors(d)).toEqual({});
		expect(toDealWatchIn(d)).toEqual({
			name: 'Europe · Jun–Aug',
			origins: ['Seattle', 'SEA'],
			destinations: ['europe', 'Lisbon', 'Porto'],
			keywords: [],
			months: [6, 7, 8],
			max_price: 60000,
			status: 'active',
			nights: 7,
			origin: null
		});
	});

	it('prices a trip length from an airport, and checks the airport', () => {
		const d = region({ nights: 14, origin: ' pdx ' });
		expect(toDealWatchIn(d)).toMatchObject({ nights: 14, origin: 'PDX' });
		expect(regionErrors(region({ origin: 'Portland' })).origin).toMatch(/three-letter/);
		expect(regionDraft({ name: 'x', nights: 0, origin: 'PDX' })).toMatchObject({
			nights: 0,
			origin: 'PDX'
		});
	});

	it('needs a region or some places', () => {
		expect(regionErrors(region({ region: '' })).region).toMatch(/region/);
		const places = region({ region: '', places: 'Lisbon, Porto, Faro' });
		expect(regionErrors(places)).toEqual({});
		expect(regionName(places)).toBe('Lisbon & 2 more');
		expect(words(' Seattle, SEA ,, Seattle ')).toEqual(['Seattle', 'SEA']);
	});

	it('finds the region again when changing one, and keeps imported keywords', () => {
		const d = regionDraft(
			{
				name: 'Hawaii',
				origins: ['Seattle'],
				destinations: ['Hawaii', 'Maui'],
				keywords: ['nonstop'],
				months: [1, 2],
				max_price: null
			},
			['hawaii', 'europe']
		);
		expect(d).toMatchObject({ region: 'hawaii', places: 'Maui', months: [1, 2], max: '' });
		expect(toDealWatchIn(d).keywords).toEqual(['nonstop']);
	});
});

describe('deal pages and settings', () => {
	it('takes an address with or without https://', () => {
		expect(asUrl('escape.flights/category/x/')).toBe('https://escape.flights/category/x/');
		expect(feedErrors({ url: 'escape.flights/category/flights-from-seattle/', name: '' })).toEqual(
			{}
		);
		expect(feedErrors({ url: 'not an address', name: '' }).url).toBeTruthy();
		expect(feedErrors({ url: '', name: '' }).url).toBeTruthy();
	});

	it('keeps the alert thresholds in range', () => {
		const ok = { dedup_days: '7', dedup_further_drop_pct: '10', percentile_alerts: false };
		expect(settingsErrors(ok)).toEqual({});
		expect(
			Object.keys(settingsErrors({ ...ok, dedup_days: '0', dedup_further_drop_pct: '95' }))
		).toEqual(['dedup_days', 'dedup_further_drop_pct']);
	});
});
