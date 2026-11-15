// The Manage forms in jsdom: what Save sends (dollars become cents), what a bad field
// says, the airport search, the search's date-pair preview and the headline test.
// `fetch` is stubbed; nothing leaves the test.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/svelte';
import { tick } from 'svelte';
import TripForm from './TripForm.svelte';
import SearchForm from './SearchForm.svelte';
import DealWatchForm from './DealWatchForm.svelte';
import FeedForm from './FeedForm.svelte';
import AirportPicker from './AirportPicker.svelte';
import type { Trip } from '../types.gen';

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const AIRPORTS: Record<string, { iata: string; name: string; city: string; country: string }[]> = {
	SEA: [
		{ iata: 'SEA', name: 'Seattle–Tacoma International Airport', city: 'Seattle', country: 'US' }
	],
	HND: [{ iata: 'HND', name: 'Tokyo Haneda Airport', city: 'Tokyo', country: 'JP' }],
	tok: [
		{ iata: 'HND', name: 'Tokyo Haneda Airport', city: 'Tokyo', country: 'JP' },
		{ iata: 'NRT', name: 'Narita International Airport', city: 'Narita', country: 'JP' }
	]
};

/** A fake server: airport search, plus whatever a test adds. */
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

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
	vi.useRealTimers();
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
	it('edits a trip and sends cents, keeping its status', async () => {
		server();
		const onsave = vi.fn();
		const { getByLabelText, getByRole, findByText } = render(TripForm, {
			initial: TRIP,
			onsave,
			oncancel: () => {}
		});
		expect((getByLabelText('Target price') as HTMLInputElement).value).toBe('900');
		expect(await findByText('Tokyo')).toBeTruthy(); // the chip looked its city up
		await fireEvent.input(getByLabelText('Target price'), { target: { value: '$1,050.50' } });
		await fireEvent.click(getByRole('button', { name: 'Save changes' }));
		expect(onsave).toHaveBeenCalledOnce();
		expect(onsave.mock.calls[0][0]).toMatchObject({
			origin: 'SEA',
			destination: 'HND',
			target_price: 105050,
			max_stops: 1,
			status: 'paused'
		});
	});

	it('refuses to save an empty trip and says what is missing', async () => {
		server();
		const onsave = vi.fn();
		const { getByRole, getByText, getByLabelText } = render(TripForm, {
			home: 'SEA',
			onsave,
			oncancel: () => {}
		});
		await fireEvent.click(getByRole('button', { name: 'Add trip' }));
		await settle();
		expect(onsave).not.toHaveBeenCalled();
		expect(getByText('Give it a name')).toBeTruthy();
		expect(getByText('Choose where you fly to')).toBeTruthy();
		expect(getByText('Choose the departure date')).toBeTruthy();
		expect(document.activeElement).toBe(getByLabelText('Name')); // the first bad field
	});

	it('picks an airport from the search with the keyboard', async () => {
		server();
		const onsave = vi.fn();
		const { getByLabelText, getByRole, findByRole } = render(TripForm, {
			home: 'SEA',
			onsave,
			oncancel: () => {}
		});
		await fireEvent.input(getByLabelText('Name'), { target: { value: 'Tokyo' } });
		const to = getByLabelText('To');
		await fireEvent.input(to, { target: { value: 'tok' } });
		await settle(250);
		const list = await findByRole('listbox');
		expect(list.textContent).toContain('NRT');
		await fireEvent.keyDown(to, { key: 'ArrowDown' }); // HND → NRT
		await fireEvent.keyDown(to, { key: 'Enter' });
		await fireEvent.input(getByLabelText('Depart'), { target: { value: '2099-05-01' } });
		await fireEvent.click(getByLabelText('One way'));
		await fireEvent.click(getByRole('button', { name: 'Add trip' }));
		expect(onsave.mock.calls[0][0]).toMatchObject({
			name: 'Tokyo',
			origin: 'SEA',
			destination: 'NRT',
			return_date: null,
			target_price: null,
			status: 'active'
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

describe('SearchForm', () => {
	it('shows how many date pairs it tracks before saving', async () => {
		const fetch = server((url, init) =>
			url.startsWith('/api/admin/searches/preview')
				? json({
						pairs: 26,
						total_pairs: 180,
						max_pairs: 400,
						detail_per_day: 37,
						refresh_days: 5,
						_sent: init?.body
					})
				: undefined
		);
		const onsave = vi.fn();
		const { getByLabelText, getByRole, getByTestId } = render(SearchForm, {
			home: 'SEA',
			onsave,
			oncancel: () => {}
		});
		await fireEvent.input(getByLabelText('Name'), { target: { value: 'Ski weekends' } });
		const to = getByLabelText('To (one or more)');
		await fireEvent.input(to, { target: { value: 'SLC' } });
		await fireEvent.keyDown(to, { key: 'Enter' });
		await fireEvent.click(getByRole('button', { name: 'Fri' }));
		await settle(500);
		const call = fetch.mock.calls.find(([u]) =>
			String(u).startsWith('/api/admin/searches/preview')
		);
		expect(call).toBeTruthy();
		expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
			origin: 'SEA',
			destinations: ['SLC'],
			depart_dows: ['FRI'],
			nights: 2
		});
		const note = getByTestId('search-preview').textContent ?? '';
		expect(note).toMatch(/This tracks\s*26\s*date pairs/);
		expect(note).toContain('180 of 400 in all');
		expect(note).toMatch(/about\s*5 days/);
		await fireEvent.click(getByRole('button', { name: 'Add search' }));
		expect(onsave).toHaveBeenCalledOnce();
	});

	it('warns when the search would go over the limit', async () => {
		server((url) =>
			url.startsWith('/api/admin/searches/preview')
				? json({
						pairs: 300,
						total_pairs: 450,
						max_pairs: 400,
						detail_per_day: 37,
						refresh_days: 13
					})
				: undefined
		);
		const { getByLabelText, getByRole, getByTestId } = render(SearchForm, {
			home: 'SEA',
			onsave: () => {},
			oncancel: () => {}
		});
		await fireEvent.input(getByLabelText('Name'), { target: { value: 'Everything' } });
		const to = getByLabelText('To (one or more)');
		await fireEvent.input(to, { target: { value: 'LAX' } });
		await fireEvent.keyDown(to, { key: 'Enter' });
		await fireEvent.click(getByRole('button', { name: 'Sat' }));
		await settle(500);
		expect(getByTestId('search-preview').textContent).toContain('over the limit');
	});
});

describe('DealWatchForm', () => {
	it('sends a region and words, and tries a headline', async () => {
		const fetch = server((url) =>
			url === '/api/admin/deal-watches/test'
				? json({ price: 29800, matched: 'Hawaii', matches: ['Hawaii'] })
				: undefined
		);
		const onsave = vi.fn();
		const { getByLabelText, getByRole, findByTestId } = render(DealWatchForm, {
			regions: ['europe', 'hawaii'],
			onsave,
			oncancel: () => {}
		});
		await fireEvent.input(getByLabelText('Name'), { target: { value: 'Hawaii' } });
		await fireEvent.input(getByLabelText('From (any of these words)'), {
			target: { value: 'Seattle, SEA' }
		});
		await fireEvent.change(getByLabelText('To a region'), { target: { value: 'hawaii' } });
		await fireEvent.input(getByLabelText('Maximum price'), { target: { value: '400' } });
		await fireEvent.click(getByRole('button', { name: 'Add deal watch' }));
		expect(onsave.mock.calls[0][0]).toEqual({
			name: 'Hawaii',
			origins: ['Seattle', 'SEA'],
			destinations: ['hawaii'],
			max_price: 40000,
			status: 'active'
		});

		await fireEvent.input(getByLabelText('Headline'), {
			target: { value: 'Alaska: Seattle – Honolulu. $298' }
		});
		await fireEvent.click(getByRole('button', { name: 'Try it' }));
		const result = await findByTestId('headline-result');
		expect(result.textContent).toContain('$298');
		expect(result.textContent).toContain('It would alert for Hawaii');
		const [, init] = fetch.mock.calls.find(([u]) => u === '/api/admin/deal-watches/test')!;
		expect(JSON.parse(String(init?.body))).toEqual({
			headline: 'Alaska: Seattle – Honolulu. $298'
		});
	});
});

describe('FeedForm', () => {
	it('wants a web address', async () => {
		const onsave = vi.fn();
		const { getByLabelText, getByRole, getByText } = render(FeedForm, {
			onsave,
			oncancel: () => {}
		});
		await fireEvent.input(getByLabelText('Feed address'), { target: { value: 'deals.example' } });
		await fireEvent.click(getByRole('button', { name: 'Add feed' }));
		expect(onsave).not.toHaveBeenCalled();
		expect(getByText(/not a web address/)).toBeTruthy();
		await fireEvent.input(getByLabelText('Feed address'), {
			target: { value: ' https://deals.example.com/feed ' }
		});
		await fireEvent.click(getByRole('button', { name: 'Add feed' }));
		expect(onsave).toHaveBeenCalledWith({
			url: 'https://deals.example.com/feed',
			name: null,
			status: 'active'
		});
	});
});
