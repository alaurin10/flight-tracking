// The Manage forms' checks, and how their fields become the API's shapes.
import { describe, expect, it } from 'vitest';
import {
	centsToDollars,
	dealWatchDraft,
	dealWatchErrors,
	feedErrors,
	parseDollars,
	searchDraft,
	searchErrors,
	settingsErrors,
	toDealWatchIn,
	toSearchIn,
	toTripIn,
	tripDraft,
	tripErrors,
	valid,
	words,
	type SearchDraft,
	type TripDraft
} from './validate';

const TODAY = '2027-01-10';

describe('money', () => {
	it('reads dollars as typed and sends cents', () => {
		expect(parseDollars('612')).toEqual({ cents: 61200 });
		expect(parseDollars(' $1,250 ')).toEqual({ cents: 125000 });
		expect(parseDollars('612.5')).toEqual({ cents: 61250 });
		expect(parseDollars('0.99')).toEqual({ cents: 99 });
		expect(parseDollars('')).toEqual({ cents: null });
	});

	it('refuses what is not an amount', () => {
		for (const t of ['-5', 'abc', '12.345', '1e3', '200000'])
			expect(parseDollars(t).error).toBeTruthy();
	});

	it('shows cents only when there are some', () => {
		expect(centsToDollars(61200)).toBe('612');
		expect(centsToDollars(61250)).toBe('612.50');
		expect(centsToDollars(null)).toBe('');
	});
});

const trip = (over: Partial<TripDraft> = {}): TripDraft => ({
	...tripDraft(undefined, 'SEA'),
	name: 'Tokyo in spring',
	destination: 'HND',
	depart_date: '2027-04-10',
	return_date: '2027-04-24',
	...over
});

describe('trip form', () => {
	it('accepts a complete trip and sends it in the API shape', () => {
		const d = trip({ target: '900', max_stops: '1', notes: '  cherry blossoms ' });
		expect(tripErrors(d, TODAY)).toEqual({});
		expect(toTripIn(d)).toEqual({
			name: 'Tokyo in spring',
			origin: 'SEA',
			destination: 'HND',
			depart_date: '2027-04-10',
			return_date: '2027-04-24',
			target_price: 90000,
			max_stops: 1,
			notes: 'cherry blossoms',
			status: 'active'
		});
	});

	it('says what is missing or wrong, field by field', () => {
		const e = tripErrors(
			trip({ name: ' ', destination: 'SEA', depart_date: '2027-01-09', target: 'lots' }),
			TODAY
		);
		expect(Object.keys(e).sort()).toEqual(['depart_date', 'destination', 'name', 'target']);
		expect(e.destination).toMatch(/same as the origin/);
		expect(e.depart_date).toMatch(/passed/);
	});

	it('needs the return after the departure, unless it is one way', () => {
		expect(tripErrors(trip({ return_date: '2027-04-10' }), TODAY).return_date).toMatch(/after/);
		expect(tripErrors(trip({ return_date: '' }), TODAY).return_date).toMatch(/one way/);
		const oneWay = trip({ oneWay: true, return_date: '2027-04-01' });
		expect(valid(tripErrors(oneWay, TODAY))).toBe(true);
		expect(toTripIn(oneWay).return_date).toBeNull();
	});

	it('refuses a date that does not exist', () => {
		expect(tripErrors(trip({ depart_date: '2027-02-30' }), TODAY).depart_date).toMatch(/not a/);
	});

	it('round-trips a stored trip through the form', () => {
		const stored = toTripIn(trip({ target: '612.50', oneWay: true }), 'paused');
		expect(toTripIn(tripDraft(stored), 'paused')).toEqual(stored);
	});
});

const search = (over: Partial<SearchDraft> = {}): SearchDraft => ({
	...searchDraft(undefined, 'SEA'),
	name: 'Ski weekends',
	destinations: ['SLC', 'DEN'],
	dows: ['FRI', 'THU'],
	...over
});

describe('flexible search form', () => {
	it('accepts a rolling search and sends weekdays in week order', () => {
		const d = search({ target: '250' });
		expect(searchErrors(d, TODAY)).toEqual({});
		expect(toSearchIn(d)).toMatchObject({
			origin: 'SEA',
			destinations: ['SLC', 'DEN'],
			depart_dows: ['THU', 'FRI'],
			nights: 2,
			window_start: null,
			window_end: null,
			target_price: 25000,
			max_stops: null
		});
	});

	it('needs a destination, a weekday and a sensible number of nights', () => {
		const e = searchErrors(search({ destinations: [], dows: [], nights: '2.5' }), TODAY);
		expect(Object.keys(e).sort()).toEqual(['destinations', 'dows', 'nights']);
		expect(searchErrors(search({ nights: '61' }), TODAY).nights).toBeTruthy();
		expect(searchErrors(search({ nights: '0' }), TODAY)).toEqual({}); // one way
		expect(searchErrors(search({ destinations: ['SEA'] }), TODAY).destinations).toMatch(
			/both the origin/
		);
	});

	it('checks a fixed window', () => {
		const e = searchErrors(
			search({ rolling: false, window_start: '2027-03-01', window_end: '2027-02-01' }),
			TODAY
		);
		expect(e.window_end).toMatch(/before it starts/);
		expect(searchErrors(search({ rolling: false }), TODAY)).toMatchObject({
			window_start: expect.any(String),
			window_end: expect.any(String)
		});
		const ok = search({ rolling: false, window_start: '2027-02-01', window_end: '2027-03-31' });
		expect(searchErrors(ok, TODAY)).toEqual({});
		expect(toSearchIn(ok)).toMatchObject({ window_start: '2027-02-01', window_end: '2027-03-31' });
	});
});

describe('deal watch form', () => {
	it('splits words on commas and puts the region first', () => {
		expect(words(' Seattle, SEA ,, Seattle ')).toEqual(['Seattle', 'SEA']);
		const d = {
			name: 'Hawaii',
			origins: 'Seattle, SEA',
			destinations: 'Maui',
			region: 'hawaii',
			max: '400'
		};
		expect(dealWatchErrors(d)).toEqual({});
		expect(toDealWatchIn(d)).toEqual({
			name: 'Hawaii',
			origins: ['Seattle', 'SEA'],
			destinations: ['hawaii', 'Maui'],
			max_price: 40000,
			status: 'active'
		});
	});

	it('finds the region again when editing', () => {
		const d = dealWatchDraft(
			{ name: 'Hawaii', origins: ['Seattle'], destinations: ['Hawaii', 'Maui'], max_price: null },
			['hawaii', 'europe']
		);
		expect(d).toMatchObject({ region: 'hawaii', destinations: 'Maui', max: '' });
	});

	it('refuses a watch that would match every deal', () => {
		const e = dealWatchErrors({ name: 'All', origins: '', destinations: '', region: '', max: '' });
		expect(e.origins).toMatch(/every deal/);
	});
});

describe('feed and settings forms', () => {
	it('wants a web address', () => {
		expect(feedErrors({ url: 'https://www.theflightdeal.com/feed/', name: '' })).toEqual({});
		expect(feedErrors({ url: 'theflightdeal.com/feed', name: '' }).url).toMatch(/https/);
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
