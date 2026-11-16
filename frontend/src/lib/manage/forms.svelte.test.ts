// The Manage forms in jsdom: what saving sends (dollars become cents, names are made
// up), what a missing field says, the airport search, the date count before saving a
// flexible search, the region's months and the headline test. `fetch` is stubbed;
// nothing leaves the test.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/svelte';
import { tick } from 'svelte';
import TripForm from './TripForm.svelte';
import SearchForm from './SearchForm.svelte';
import RegionForm from './RegionForm.svelte';
import FeedForm from './FeedForm.svelte';
import AirportPicker from './AirportPicker.svelte';
import RowMenu from './RowMenu.svelte';
import type { Trip } from '../types.gen';

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const AIRPORTS: Record<string, { iata: string; name: string; city: string; country: string }[]> = {
	SEA: [
		{ iata: 'SEA', name: 'Seattle–Tacoma International Airport', city: 'Seattle', country: 'US' }
	],
	HND: [{ iata: 'HND', name: 'Tokyo Haneda Airport', city: 'Tokyo', country: 'JP' }],
	SLC: [
		{
			iata: 'SLC',
			name: 'Salt Lake City International Airport',
			city: 'Salt Lake City',
			country: 'US'
		}
	],
	tok: [
		{ iata: 'HND', name: 'Tokyo Haneda Airport', city: 'Tokyo', country: 'JP' },
		{ iata: 'NRT', name: 'Narita International Airport', city: 'Narita', country: 'JP' }
	]
};

/** A fake server: the airport search, plus whatever a test adds. */
function server(
	extra: (url: string, init?: RequestInit) => Response | undefined = () => undefined
) {
	const fetch = vi.fn(async (url: string, init?: RequestInit) => {
		const hit = extra(url, init);
		if (hit) return hit;
		const q = new URL(url, 'http://x').searchParams.get('q');
		if (url.startsWith('/api/airports')) return json(AIRPORTS[q ?? ''] ?? []);
		return json({ detail: 'not found' }, 404);
	});
	vi.stubGlobal('fetch', fetch);
	return fetch;
}

async function settle(ms = 0) {
	if (ms) await new Promise((r) => setTimeout(r, ms));
	for (let i = 0; i < 6; i++) {
		await Promise.resolve();
		await tick();
	}
}

/** Type into an airport field and pick the first match. */
async function pickAirport(input: HTMLElement, text: string) {
	await fireEvent.input(input, { target: { value: text } });
	await settle(250);
	await fireEvent.keyDown(input, { key: 'Enter' });
	await settle();
}

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

const TRIP: Trip = {
	id: 3,
	slug: 'tokyo-spring',
	name: 'Tokyo in spring',
	origin: 'SEA',
	destination: 'HND',
	depart_date: '2099-04-10',
	return_date: '2099-04-24',
	target_price: 90000,
	max_stops: 1,
	notes: null,
	status: 'paused',
	created_at: '2026-11-15T00:00:00Z',
	updated_at: '2026-11-15T00:00:00Z'
};

describe('TripForm', () => {
	it('needs only where and when, and names the trip itself', async () => {
		server();
		const onsave = vi.fn();
		const { getByLabelText, getByRole } = render(TripForm, {
			home: 'SEA',
			onsave,
			oncancel: () => {}
		});
		await pickAirport(getByLabelText('Where to'), 'tok'); // Tokyo Haneda, the first match
		await fireEvent.input(getByLabelText('Leave'), { target: { value: '2099-05-01' } });
		await fireEvent.input(getByLabelText('Come back'), { target: { value: '2099-05-08' } });
		await fireEvent.input(getByLabelText('Alert me when it’s under'), {
			target: { value: '$1,050' }
		});
		await fireEvent.click(getByRole('button', { name: 'Start watching' }));
		expect(onsave).toHaveBeenCalledOnce();
		expect(onsave.mock.calls[0][0]).toEqual({
			name: 'Tokyo · May 1',
			origin: 'SEA',
			destination: 'HND',
			depart_date: '2099-05-01',
			return_date: '2099-05-08',
			target_price: 105000,
			max_stops: null,
			notes: null,
			status: 'active'
		});
	});

	it('refuses to save without a destination and dates, and says so', async () => {
		server();
		const onsave = vi.fn();
		const { getByRole, getByText, getByLabelText } = render(TripForm, {
			home: 'SEA',
			onsave,
			oncancel: () => {}
		});
		await fireEvent.click(getByRole('button', { name: 'Start watching' }));
		await settle();
		expect(onsave).not.toHaveBeenCalled();
		expect(getByText('Choose where you want to go')).toBeTruthy();
		expect(getByText('Choose the day you leave')).toBeTruthy();
		expect(document.activeElement).toBe(getByLabelText('Where to')); // the first bad field
	});

	it('keeps a stored trip’s name, options and status when changed', async () => {
		server();
		const onsave = vi.fn();
		const { getByLabelText, getByRole, findByText } = render(TripForm, {
			initial: TRIP,
			home: 'SEA',
			onsave,
			oncancel: () => {}
		});
		expect(await findByText('Tokyo')).toBeTruthy(); // the chip looked its city up
		expect((getByLabelText('Stops') as HTMLSelectElement).value).toBe('1'); // More options open
		await fireEvent.input(getByLabelText('Alert me when it’s under'), { target: { value: '850' } });
		await fireEvent.click(getByRole('button', { name: 'Save changes' }));
		expect(onsave.mock.calls[0][0]).toMatchObject({
			name: 'Tokyo in spring',
			target_price: 85000,
			max_stops: 1,
			status: 'paused'
		});
	});
});

describe('AirportPicker', () => {
	it('takes a typed code with Enter, and Backspace removes the last one', async () => {
		server();
		const { getByLabelText, getByText, queryByText } = render(AirportPicker, {
			id: 'to',
			label: 'To',
			value: [],
			multiple: true
		});
		const input = getByLabelText('To');
		await fireEvent.input(input, { target: { value: 'slc' } });
		await fireEvent.keyDown(input, { key: 'Enter' });
		await fireEvent.input(input, { target: { value: 'den' } });
		await fireEvent.keyDown(input, { key: 'Enter' });
		expect(getByText('SLC')).toBeTruthy();
		expect(getByText('DEN')).toBeTruthy();
		await fireEvent.keyDown(input, { key: 'Backspace' });
		expect(queryByText('DEN')).toBeNull();
	});
});

const PREVIEW = { pairs: 4, total_pairs: 98, max_pairs: 400, detail_per_day: 37, refresh_days: 3 };

describe('SearchForm', () => {
	it('is weekends next month by default, and says how many dates that is', async () => {
		const fetch = server((url) =>
			url.startsWith('/api/admin/searches/preview') ? json(PREVIEW) : undefined
		);
		const onsave = vi.fn();
		const { getByLabelText, getByRole, getByTestId } = render(SearchForm, {
			home: 'SEA',
			onsave,
			oncancel: () => {}
		});
		expect(getByRole('button', { name: /Weekend/ }).getAttribute('aria-pressed')).toBe('true');
		await pickAirport(getByLabelText('Where to'), 'SLC');
		await settle(500);
		const call = fetch.mock.calls.find(([u]) =>
			String(u).startsWith('/api/admin/searches/preview')
		);
		const sent = JSON.parse(String(call?.[1]?.body));
		expect(sent).toMatchObject({
			name: expect.stringMatching(/^Salt Lake City weekends · /),
			origin: 'SEA',
			destinations: ['SLC'],
			depart_dows: ['FRI'],
			nights: 2
		});
		expect(sent.window_start).toMatch(/-01$/);
		const note = getByTestId('search-preview').textContent ?? '';
		expect(note).toMatch(/That’s\s*4\s*dates to price/);
		expect(note).toMatch(/about every\s*3 days/);
		await fireEvent.click(getByRole('button', { name: 'Start watching' }));
		expect(onsave).toHaveBeenCalledOnce();
	});

	it('a week, any time; custom shows the days and nights', async () => {
		server((url) => (url.startsWith('/api/admin/searches/preview') ? json(PREVIEW) : undefined));
		const onsave = vi.fn();
		const { getByLabelText, getByRole, queryByLabelText } = render(SearchForm, {
			home: 'SEA',
			onsave,
			oncancel: () => {}
		});
		await pickAirport(getByLabelText('Where to'), 'SLC');
		expect(queryByLabelText('Nights')).toBeNull();
		await fireEvent.click(getByRole('button', { name: /A week/ }));
		await fireEvent.click(getByRole('button', { name: 'Any time' }));
		await fireEvent.click(getByRole('button', { name: 'Start watching' }));
		expect(onsave.mock.calls[0][0]).toMatchObject({
			depart_dows: ['SAT'],
			nights: 7,
			window_start: null,
			window_end: null
		});
		await fireEvent.click(getByRole('button', { name: /Custom/ }));
		expect(getByLabelText('Nights')).toBeTruthy();
	});

	it('warns when it would go over the limit', async () => {
		server((url) =>
			url.startsWith('/api/admin/searches/preview')
				? json({ ...PREVIEW, pairs: 300, total_pairs: 450 })
				: undefined
		);
		const { getByLabelText, getByTestId } = render(SearchForm, {
			home: 'SEA',
			onsave: () => {},
			oncancel: () => {}
		});
		await pickAirport(getByLabelText('Where to'), 'SLC');
		await settle(500);
		expect(getByTestId('search-preview').textContent).toContain('over the limit');
	});
});

describe('RegionForm', () => {
	it('a region in the summer under a price, from home', async () => {
		server();
		const onsave = vi.fn();
		const { getByRole, getByLabelText, getByText } = render(RegionForm, {
			regions: ['europe', 'hawaii'],
			homeWords: 'Seattle, SEA',
			onsave,
			oncancel: () => {}
		});
		await fireEvent.click(getByRole('button', { name: 'Europe' }));
		await fireEvent.click(getByRole('button', { name: 'Summer' }));
		expect(getByText(/Posts for Jun–Aug/)).toBeTruthy();
		await fireEvent.input(getByLabelText('Alert me when it’s under'), { target: { value: '600' } });
		await fireEvent.click(getByRole('button', { name: 'Start watching' }));
		expect(onsave.mock.calls[0][0]).toEqual({
			name: 'Europe · Jun–Aug',
			origins: ['Seattle', 'SEA'],
			destinations: ['europe'],
			keywords: [],
			months: [6, 7, 8],
			max_price: 60000,
			status: 'active'
		});
	});

	it('needs a region or places, and tries a headline', async () => {
		const fetch = server((url) =>
			url === '/api/admin/deal-watches/test'
				? json({
						price: 41200,
						months: [6, 7, 8],
						matched: 'Europe · Jun–Aug',
						matches: ['Europe · Jun–Aug']
					})
				: undefined
		);
		const onsave = vi.fn();
		const { getByRole, getByText, getByLabelText, findByTestId } = render(RegionForm, {
			regions: ['europe'],
			homeWords: 'Seattle, SEA',
			onsave,
			oncancel: () => {}
		});
		await fireEvent.click(getByRole('button', { name: 'Start watching' }));
		await settle();
		expect(onsave).not.toHaveBeenCalled();
		expect(getByText('Choose a region, or name some places')).toBeTruthy();

		await fireEvent.click(getByText('More options'));
		await fireEvent.input(getByLabelText('Try a headline'), {
			target: { value: 'Seattle to Paris $412 [Jun-Aug]' }
		});
		await fireEvent.click(getByRole('button', { name: 'Try it' }));
		const result = await findByTestId('headline-result');
		expect(result.textContent).toMatch(/\$412, for Jun–Aug/);
		expect(result.textContent).toContain('It would alert for Europe · Jun–Aug');
		const [, init] = fetch.mock.calls.find(([u]) => u === '/api/admin/deal-watches/test')!;
		expect(JSON.parse(String(init?.body))).toEqual({ headline: 'Seattle to Paris $412 [Jun-Aug]' });
	});
});

describe('FeedForm', () => {
	it('takes a page address as pasted, adding https://', async () => {
		const onsave = vi.fn();
		const { getByLabelText, getByRole, getByText } = render(FeedForm, {
			onsave,
			oncancel: () => {}
		});
		await fireEvent.input(getByLabelText('Page address'), { target: { value: 'not an address' } });
		await fireEvent.click(getByRole('button', { name: 'Start watching' }));
		expect(onsave).not.toHaveBeenCalled();
		expect(getByText(/not a web address/)).toBeTruthy();
		await fireEvent.input(getByLabelText('Page address'), {
			target: { value: ' escape.flights/category/flights-from-seattle/ ' }
		});
		await fireEvent.click(getByRole('button', { name: 'Start watching' }));
		expect(onsave).toHaveBeenCalledWith({
			url: 'https://escape.flights/category/flights-from-seattle/',
			name: null,
			status: 'active'
		});
	});
});

describe('RowMenu', () => {
	it('opens, runs an item, and closes on Escape', async () => {
		const pause = vi.fn();
		const { getByRole, queryByRole } = render(RowMenu, {
			label: 'More for Tokyo',
			items: [
				{ label: 'Pause', run: pause },
				{ label: 'Delete', run: () => {}, danger: true }
			]
		});
		const button = getByRole('button', { name: 'More for Tokyo' });
		await fireEvent.click(button);
		await settle();
		expect(getByRole('menu')).toBeTruthy();
		expect(document.activeElement?.textContent).toBe('Pause');
		await fireEvent.keyDown(getByRole('menu'), { key: 'Escape' });
		expect(queryByRole('menu')).toBeNull();
		expect(document.activeElement).toBe(button);
		await fireEvent.click(button);
		await settle();
		await fireEvent.click(getByRole('menuitem', { name: 'Pause' }));
		expect(pause).toHaveBeenCalledOnce();
		expect(queryByRole('menu')).toBeNull();
	});
});
