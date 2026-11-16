<!-- What Flight Watch watches, for the owner. Four ways to watch, each a short form:
       a trip        a destination on set dates
       flexible      a destination, any weekend in May (say)
       a region      anywhere in Europe in the summer, from deal pages
       a deal page   a deal site's page or feed
     Below, what is being watched: tap one to change it; its ⋯ menu checks now, pauses,
     resumes or deletes. Alert settings are folded away at the bottom. Anyone can open the
     page; it asks for the owner password first (sign-in copied from Snow Watch). Forms
     open over the list with shallow routing, so the Back button closes them. -->
<script lang="ts">
	import { page } from '$app/state';
	import { goto, pushState } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { untrack } from 'svelte';
	import {
		adminDealFeeds,
		adminDealWatches,
		adminSearches,
		adminTrips,
		deleteWatch,
		forgetOverview,
		getAlertSettings,
		getCollecting,
		queueJob,
		saveAlertSettings,
		saveDealFeed,
		saveDealWatch,
		saveSearch,
		saveTrip,
		searchAirports,
		setStatus,
		waitForJob,
		type Status,
		type WatchKind
	} from '../../lib/api';
	import { app } from '../../lib/app.svelte';
	import { friendlyError, localTimes, sentence } from '../../lib/errors';
	import { instant, monthDay, money, nights, tripDates, weekdays } from '../../lib/format';
	import { owner } from '../../lib/session.svelte';
	import { LENGTHS, monthEnd, monthsPhrase } from '../../lib/manage/validate';
	import OwnerLogin from '../../lib/manage/OwnerLogin.svelte';
	import TripForm from '../../lib/manage/TripForm.svelte';
	import SearchForm from '../../lib/manage/SearchForm.svelte';
	import RegionForm from '../../lib/manage/RegionForm.svelte';
	import FeedForm from '../../lib/manage/FeedForm.svelte';
	import AlertSettingsForm from '../../lib/manage/AlertSettingsForm.svelte';
	import ConfirmDialog from '../../lib/manage/ConfirmDialog.svelte';
	import RowMenu from '../../lib/manage/RowMenu.svelte';
	import type {
		AlertSettings,
		CollectingOut,
		DealFeed,
		DealFeedIn,
		DealWatch,
		DealWatchIn,
		Search,
		SearchIn,
		Trip,
		TripIn
	} from '../../lib/types.gen';

	type Form = NonNullable<App.PageState['manage']>['form'];

	const WAYS: { form: Form; title: string; example: string; icon: string }[] = [
		{
			form: 'trip',
			title: 'A trip',
			example: 'Set dates; the cheapest flight',
			icon: 'M2 14.5l20-8-6 10.5-3.5-4.5-4.5 2.5-2-2 4-2.5z'
		},
		{
			form: 'search',
			title: 'Flexible dates',
			example: 'Any weekend in May',
			icon: 'M4 6h16v14H4zM4 10h16M9 3v4M15 3v4'
		},
		{
			form: 'deal-watch',
			title: 'A region',
			example: 'Anywhere in Europe this summer',
			icon: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z'
		},
		{
			form: 'feed',
			title: 'A deal page',
			example: 'Sites you follow, like escape.flights',
			icon: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1'
		}
	];

	let trips = $state<Trip[]>([]);
	let searches = $state<Search[]>([]);
	let regions = $state<DealWatch[]>([]);
	let pages = $state<DealFeed[]>([]);
	let collecting = $state<CollectingOut | null>(null);
	let settings = $state<AlertSettings | null>(null);
	let homeWords = $state('');
	let loaded = $state(false);
	let error = $state<string | null>(null);
	let formError = $state<string | null>(null);
	let saving = $state(false);
	let settingsNote = $state<string | null>(null);
	// Per row ("trip:3"): what its check or save is doing, and a link once done.
	let progress = $state<Record<string, { text: string; bad?: boolean; href?: string }>>({});
	let asking = $state<{ kind: WatchKind; id: number; name: string } | null>(null);

	const open = $derived(page.state.manage ?? null);
	const home = $derived(app.meta?.home_airport ?? '');
	const cur = $derived(app.currency);
	const leaving = new AbortController();

	async function load() {
		error = null;
		try {
			const me = owner.me ?? (await owner.load());
			if (!me.is_owner) return;
			[trips, searches, regions, pages, collecting, settings] = await Promise.all([
				adminTrips(),
				adminSearches(),
				adminDealWatches(),
				adminDealFeeds(),
				getCollecting(),
				getAlertSettings()
			]);
			loaded = true;
		} catch (e) {
			if (!owner.ended(e)) error = friendlyError(e);
		}
	}

	// Once, on arrival: `load` reads the session, and an effect that tracked it would run
	// again on sign-in, and its clean-up would stop following the jobs.
	$effect(() => {
		untrack(load);
		return () => leaving.abort();
	});

	// A found deal's "Track this" (/manage?to=LIS&depart=…): open a new trip with its
	// fields, once (Back from the form returns here without opening it again).
	let prefillUsed = false;
	$effect(() => {
		if (prefillUsed || !owner.me?.is_owner || !loaded) return;
		const q = page.url.searchParams;
		const to = q.get('to');
		if (!to) return;
		prefillUsed = true;
		formError = null;
		pushState('', {
			manage: {
				form: 'trip',
				prefill: {
					name: q.get('name') ?? undefined,
					origin: q.get('from') ?? undefined,
					destination: to,
					depart_date: q.get('depart') ?? undefined,
					return_date: q.get('return')
				}
			}
		});
	});

	// Home's words for a region ("Seattle, SEA"): what a deal post says it leaves from.
	$effect(() => {
		const code = home;
		if (!code) return;
		homeWords = code;
		searchAirports(code)
			.then((found) => {
				const a = found.find((x) => x.iata === code);
				if (a?.city) homeWords = `${a.city}, ${code}`;
			})
			.catch(() => {});
	});

	async function act(work: () => Promise<void>) {
		error = null;
		try {
			await work();
		} catch (e) {
			if (!owner.ended(e)) error = friendlyError(e);
		}
	}

	function openForm(form: Form, id?: number) {
		formError = null;
		pushState('', { manage: { form, id } });
	}
	function closeForm() {
		formError = null;
		if (page.state.manage) history.back();
	}

	async function changed() {
		forgetOverview();
		await load();
	}

	// -- fetch jobs: "check now", and the first price after saving ----------------------

	const say = (key: string, text: string, extra: { bad?: boolean; href?: string } = {}) =>
		(progress[key] = { text, ...extra });

	async function follow(key: string, jobId: number, name: string, href: string) {
		say(key, `Getting prices for ${name}…`);
		try {
			const job = await waitForJob(jobId, undefined, leaving.signal);
			if (leaving.signal.aborted) return;
			if (job.status === 'done') say(key, 'Fresh prices are in.', { href });
			else if (job.status === 'failed')
				say(key, sentence(localTimes(job.error ?? 'it failed for an unknown reason')), {
					bad: true
				});
			else say(key, 'Still waiting for the collector; prices will appear on its page.');
			forgetOverview();
		} catch (e) {
			if (!leaving.signal.aborted && !owner.ended(e)) say(key, friendlyError(e), { bad: true });
		}
	}

	const tripHref = (slug: string) => resolve('/trip/[slug]', { slug });
	const searchHref = (slug: string) => resolve('/search/[slug]', { slug });

	async function checkNow(kind: 'trip' | 'search', slug: string, id: number, name: string) {
		const key = `${kind}:${id}`;
		try {
			collecting = await getCollecting();
			if (collecting.cooldown_until && Date.parse(collecting.cooldown_until) > Date.now()) {
				say(
					key,
					`Collecting is paused until ${instant(collecting.cooldown_until)} (Google asked us to slow down); this goes first when it resumes.`,
					{ bad: true }
				);
				return;
			}
			const job = await queueJob(
				kind === 'trip'
					? { kind: 'fetch_trip', trip: slug }
					: { kind: 'fetch_search', search: slug }
			);
			void follow(key, job.id, name, kind === 'trip' ? tripHref(slug) : searchHref(slug));
		} catch (e) {
			if (!owner.ended(e)) say(key, friendlyError(e), { bad: true });
		}
	}

	// -- saving ---------------------------------------------------------------------------

	async function save(work: () => Promise<void>) {
		saving = true;
		formError = null;
		try {
			await work();
			closeForm();
			await changed();
		} catch (e) {
			if (!owner.ended(e)) formError = friendlyError(e);
		} finally {
			saving = false;
		}
	}

	const onTrip = (body: TripIn) =>
		save(async () => {
			const r = await saveTrip(body, open?.id);
			if (r.job_id) void follow(`trip:${r.trip.id}`, r.job_id, r.trip.name, tripHref(r.trip.slug));
		});
	const onSearch = (body: SearchIn) =>
		save(async () => {
			const r = await saveSearch(body, open?.id);
			if (r.job_id)
				void follow(`search:${r.search.id}`, r.job_id, r.search.name, searchHref(r.search.slug));
		});
	const onRegion = (body: DealWatchIn) =>
		save(async () => {
			const w = await saveDealWatch(body, open?.id);
			say(`region:${w.id}`, 'Saved. Matching posts from your deal pages will alert you.');
		});
	const onPage = (body: DealFeedIn) =>
		save(async () => {
			const f = await saveDealFeed(body, open?.id);
			const how =
				f.kind === 'page'
					? 'It has no feed, so its posts are read from the page.'
					: f.url !== body.url
						? `Found its feed (${f.url}).`
						: 'Read as a feed.';
			say(`page:${f.id}`, `${how} New posts show on Deals.`);
		});

	const status = (kind: WatchKind, id: number, to: Status, name: string) =>
		act(async () => {
			const r = (await setStatus(kind, id, to)) as {
				job_id?: number | null;
				trip?: Trip;
				search?: Search;
			};
			await changed();
			if (r.job_id && r.trip) void follow(`trip:${id}`, r.job_id, name, tripHref(r.trip.slug));
			if (r.job_id && r.search)
				void follow(`search:${id}`, r.job_id, name, searchHref(r.search.slug));
		});

	const remove = () =>
		act(async () => {
			if (!asking) return;
			const { kind, id } = asking;
			asking = null;
			await deleteWatch(kind, id);
			await changed();
		});

	async function onSettings(body: AlertSettings) {
		saving = true;
		settingsNote = null;
		try {
			settings = await saveAlertSettings(body);
			settingsNote = 'Saved. The next alert check uses them.';
		} catch (e) {
			if (!owner.ended(e)) settingsNote = friendlyError(e);
		} finally {
			saving = false;
		}
	}

	const signOut = () =>
		act(async () => {
			await owner.signOut();
			loaded = false;
		});

	// -- words for the rows -----------------------------------------------------------------

	const under = (cents: number | null | undefined) =>
		cents == null ? '' : ` · under ${money(cents, cur)}`;
	const title = (r: string) => r.replace(/\b\w/g, (c) => c.toUpperCase());

	const LENGTH_WORDS: Record<string, string> = {
		weekend: 'weekends',
		long: 'long weekends',
		week: 'week-long trips'
	};
	function searchLength(s: Search): string {
		for (const [k, v] of Object.entries(LENGTHS))
			if (
				v.nights === s.nights &&
				v.dows.length === s.depart_dows.length &&
				v.dows.every((x) => s.depart_dows.includes(x))
			)
				return LENGTH_WORDS[k];
		return `${weekdays(s.depart_dows)}, ${nights(s.nights)}`;
	}
	function searchWhen(s: Search): string {
		const a = s.window_start;
		const b = s.window_end;
		if (!a && !b) return 'any time';
		if (a && b && a.endsWith('-01') && monthEnd(b.slice(0, 7)) === b) {
			const ms = [Number(a.slice(5, 7)), Number(b.slice(5, 7))];
			return a.slice(0, 7) === b.slice(0, 7)
				? `${monthsPhrase([ms[0]])} ${a.slice(0, 4)}`
				: `${monthsPhrase([ms[0]])}–${monthsPhrase([ms[1]])} ${b.slice(0, 4)}`;
		}
		return `${a ? monthDay(a) : '…'} – ${b ? monthDay(b) : '…'}`;
	}
	function regionWhere(w: DealWatch): string {
		const d = w.destinations ?? [];
		if (!d.length) return 'anywhere';
		return d.map((x) => (collecting?.regions.includes(x.toLowerCase()) ? title(x) : x)).join(', ');
	}
	function shortUrl(u: string): string {
		try {
			const x = new URL(u);
			return (x.host.replace(/^www\./, '') + x.pathname).replace(/\/$/, '');
		} catch {
			return u;
		}
	}

	const find = <T extends { id: number }>(list: T[], id?: number) =>
		id === undefined ? undefined : list.find((x) => x.id === id);
	const cooling = $derived(
		collecting?.cooldown_until && Date.parse(collecting.cooldown_until) > Date.now()
			? collecting.cooldown_until
			: null
	);
	const nothing = $derived(!trips.length && !searches.length && !regions.length && !pages.length);
	const kindName: Record<WatchKind, string> = {
		trips: 'trip',
		searches: 'flexible-dates search',
		'deal-watches': 'region',
		'deal-feeds': 'deal page'
	};
	const FORM_TITLE: Record<Form, [string, string]> = {
		trip: ['Watch a trip', 'Where and when you’re going; Flight Watch finds the cheapest flight.'],
		search: [
			'Watch flexible dates',
			'Somewhere you’d go on any of several dates; Flight Watch finds the cheapest.'
		],
		'deal-watch': [
			'Watch a region',
			'Anywhere in a part of the world. Deals come from your deal pages and feeds.'
		],
		feed: [
			'Watch a deal page',
			'A deal site you check. New posts show on Deals; those that match what you watch alert you.'
		]
	};
</script>

<svelte:head><title>Manage · Flight Watch</title></svelte:head>

<header class="pagehead head">
	<div>
		<h1>Manage</h1>
		<p>What Flight Watch watches for you.</p>
	</div>
	{#if owner.isOwner}<button class="btn" onclick={signOut}>Sign out</button>{/if}
</header>

{#if error}<p class="notice bad top" role="alert">{error}</p>{/if}

{#snippet progressLine(key: string)}
	{@const p = progress[key]}
	{#if p}
		<span class="small job" class:bad={p.bad} role="status">
			{p.text}
			{#if p.href}
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve() -->
				<a href={p.href}>Open it</a>{/if}
		</span>
	{/if}
{/snippet}

{#snippet statusChip(st: string)}
	{#if st !== 'active'}<span class="pill neutral">{st.charAt(0).toUpperCase() + st.slice(1)}</span
		>{/if}
{/snippet}

{#if owner.me && !owner.me.is_owner}
	{#if owner.me.owner_login_enabled}
		<OwnerLogin
			onSignedIn={(m) => {
				owner.signedIn(m);
				load();
			}}
		/>
	{:else}
		<p class="card">
			Managing what is watched is turned off on this server: set <code>OWNER_PASSWORD</code> (12 or
			more characters) to turn it on. <a href={resolve('/')}>Back to the overview</a>
		</p>
	{/if}
{:else if !owner.me || !loaded}
	{#if !error}<p class="card loading" aria-live="polite">Loading…</p>{/if}
{:else if open}
	{@const [heading, intro] = FORM_TITLE[open.form]}
	<section class="card formcard" aria-labelledby="form-h">
		{#if open.form === 'trip'}
			{@const t = find(trips, open.id)}
			<h2 id="form-h">{t ? `Change ${t.name}` : heading}</h2>
			{#if !t}<p class="muted intro">{intro}</p>{/if}
			<TripForm
				initial={t}
				prefill={open.prefill}
				{home}
				{saving}
				onsave={onTrip}
				oncancel={closeForm}
			/>
		{:else if open.form === 'search'}
			{@const s = find(searches, open.id)}
			<h2 id="form-h">{s ? `Change ${s.name}` : heading}</h2>
			{#if !s}<p class="muted intro">{intro}</p>{/if}
			<SearchForm initial={s} {home} {saving} onsave={onSearch} oncancel={closeForm} />
		{:else if open.form === 'deal-watch'}
			{@const w = find(regions, open.id)}
			<h2 id="form-h">{w ? `Change ${w.name}` : heading}</h2>
			{#if !w}<p class="muted intro">{intro}</p>{/if}
			<RegionForm
				initial={w}
				regions={collecting?.regions ?? []}
				{homeWords}
				{saving}
				onsave={onRegion}
				oncancel={closeForm}
			/>
		{:else}
			{@const f = find(pages, open.id)}
			<h2 id="form-h">{f ? 'Change deal page' : heading}</h2>
			{#if !f}<p class="muted intro">{intro}</p>{/if}
			<FeedForm initial={f} {saving} onsave={onPage} oncancel={closeForm} />
		{/if}
		{#if formError}<p class="notice bad formerr" role="alert">{formError}</p>{/if}
	</section>
{:else}
	{#if cooling}
		<p class="notice caution top" role="status">
			Collecting is paused until {instant(cooling)} because Google asked us to slow down. Changes are
			saved; their prices come when it resumes.
		</p>
	{/if}

	<section aria-labelledby="add-h">
		<h2 class="section" id="add-h">Watch for</h2>
		<div class="ways">
			{#each WAYS as w (w.form)}
				<button class="way card" onclick={() => openForm(w.form)}>
					<span class="wicon" aria-hidden="true"
						><svg viewBox="0 0 24 24" width="22" height="22"><path d={w.icon} /></svg></span
					>
					<span class="wtext"><strong>{w.title}</strong><span>{w.example}</span></span>
				</button>
			{/each}
		</div>
	</section>

	<h2 class="section">What you’re watching</h2>
	{#if nothing && !collecting?.default_feeds.length}
		<p class="card muted empty">
			Nothing yet. Pick one of the four above; a trip’s first price arrives within a minute or so.
		</p>
	{/if}

	{#if trips.length}
		<section class="card group" aria-labelledby="trips-h">
			<h3 id="trips-h">Trips</h3>
			<ul class="rows">
				{#each trips as t (t.id)}
					{@const st = t.status ?? 'active'}
					<li class="row" class:dim={st !== 'active'} data-testid="manage-trip">
						<button class="open" onclick={() => openForm('trip', t.id)}>
							<span class="name">{t.name} {@render statusChip(st)}</span>
							<span class="faint small"
								>{t.origin} → {t.destination} · {tripDates(t.depart_date, t.return_date)}{under(
									t.target_price
								)}</span
							>
						</button>
						<RowMenu
							label={`More for ${t.name}`}
							items={[
								...(st === 'active'
									? [
											{ label: 'Check now', run: () => checkNow('trip', t.slug, t.id, t.name) },
											{ label: 'Pause', run: () => status('trips', t.id, 'paused', t.name) },
											{
												label: 'Mark as booked',
												run: () => status('trips', t.id, 'booked', t.name)
											}
										]
									: [{ label: 'Resume', run: () => status('trips', t.id, 'active', t.name) }]),
								{ label: 'Open its page', run: () => goto(tripHref(t.slug)) },
								{
									label: 'Delete',
									danger: true,
									run: () => (asking = { kind: 'trips', id: t.id, name: t.name })
								}
							]}
						/>
						{@render progressLine(`trip:${t.id}`)}
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#if searches.length}
		<section class="card group" aria-labelledby="searches-h">
			<h3 id="searches-h">Flexible dates</h3>
			<ul class="rows">
				{#each searches as s (s.id)}
					{@const st = s.status ?? 'active'}
					<li class="row" class:dim={st !== 'active'} data-testid="manage-search">
						<button class="open" onclick={() => openForm('search', s.id)}>
							<span class="name">{s.name} {@render statusChip(st)}</span>
							<span class="faint small"
								>{s.origin} → {s.destinations.join(', ')} · {searchLength(s)} · {searchWhen(
									s
								)}{under(s.target_price)}</span
							>
						</button>
						<RowMenu
							label={`More for ${s.name}`}
							items={[
								...(st === 'active'
									? [
											{ label: 'Check now', run: () => checkNow('search', s.slug, s.id, s.name) },
											{ label: 'Pause', run: () => status('searches', s.id, 'paused', s.name) }
										]
									: [{ label: 'Resume', run: () => status('searches', s.id, 'active', s.name) }]),
								{ label: 'Open its page', run: () => goto(searchHref(s.slug)) },
								{
									label: 'Delete',
									danger: true,
									run: () => (asking = { kind: 'searches', id: s.id, name: s.name })
								}
							]}
						/>
						{@render progressLine(`search:${s.id}`)}
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#if regions.length}
		<section class="card group" aria-labelledby="regions-h">
			<h3 id="regions-h">Regions</h3>
			<ul class="rows">
				{#each regions as w (w.id)}
					{@const st = w.status ?? 'active'}
					<li class="row" class:dim={st !== 'active'} data-testid="manage-watch">
						<button class="open" onclick={() => openForm('deal-watch', w.id)}>
							<span class="name">{w.name} {@render statusChip(st)}</span>
							<span class="faint small"
								>{regionWhere(w)} · {monthsPhrase(w.months ?? [])}{under(w.max_price)}{w.origins
									?.length
									? ` · from ${w.origins.join(', ')}`
									: ''}</span
							>
						</button>
						<RowMenu
							label={`More for ${w.name}`}
							items={[
								st === 'active'
									? { label: 'Pause', run: () => status('deal-watches', w.id, 'paused', w.name) }
									: { label: 'Resume', run: () => status('deal-watches', w.id, 'active', w.name) },
								{
									label: 'Delete',
									danger: true,
									run: () => (asking = { kind: 'deal-watches', id: w.id, name: w.name })
								}
							]}
						/>
						{@render progressLine(`region:${w.id}`)}
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	<section class="card group" aria-labelledby="pages-h">
		<h3 id="pages-h">Deal pages</h3>
		{#if pages.length}
			<ul class="rows">
				{#each pages as f (f.id)}
					{@const st = f.status ?? 'active'}
					{@const nm = f.name || shortUrl(f.url)}
					<li class="row" class:dim={st !== 'active'} data-testid="manage-feed">
						<button class="open" onclick={() => openForm('feed', f.id)}>
							<span class="name url">{nm} {@render statusChip(st)}</span>
							<span class="faint small url"
								>{f.name ? `${shortUrl(f.url)} · ` : ''}{f.kind === 'page'
									? 'web page'
									: 'feed'}</span
							>
						</button>
						<RowMenu
							label={`More for ${nm}`}
							items={[
								st === 'active'
									? { label: 'Pause', run: () => status('deal-feeds', f.id, 'paused', nm) }
									: { label: 'Resume', run: () => status('deal-feeds', f.id, 'active', nm) },
								{
									label: 'Delete',
									danger: true,
									run: () => (asking = { kind: 'deal-feeds', id: f.id, name: nm })
								}
							]}
						/>
						{@render progressLine(`page:${f.id}`)}
					</li>
				{/each}
			</ul>
		{:else if collecting?.default_feeds.length}
			<p class="muted small empty">
				None added, so the built-in feeds are read:
				{[...new Set(collecting.default_feeds.map((u) => shortUrl(u).split('/')[0]))].join(', ')}.
				Adding a page replaces them.
			</p>
		{:else}
			<p class="muted small empty">None yet. Regions need at least one to read.</p>
		{/if}
	</section>

	<details class="card settings">
		<summary>Alert settings</summary>
		{#if settings}
			<AlertSettingsForm initial={settings} {saving} onsave={onSettings} />
			{#if settingsNote}<p class="notice info setnote" role="status">{settingsNote}</p>{/if}
		{/if}
		{#if collecting}
			<p class="faint small limits" data-testid="limits">
				Watching {collecting.active_trips} of {collecting.max_trips} trips,
				{collecting.active_searches} of {collecting.max_searches} flexible searches and
				{collecting.active_pairs} of {collecting.max_pairs} dates; {collecting.budget.used} of
				{collecting.budget.limit} Google checks used in the last 24 hours.
			</p>
		{/if}
	</details>
{/if}

{#if asking}
	<ConfirmDialog
		title={`Delete ${asking.name}?`}
		body={`The ${kindName[asking.kind]} is removed. Prices already collected are kept, and adding it again picks its history back up. To stop it for a while, Pause it instead.`}
		confirm="Delete"
		danger
		onconfirm={remove}
		oncancel={() => (asking = null)}
	/>
{/if}

<style>
	.head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 12px;
	}
	.head .btn {
		margin-top: 8px;
	}
	.top {
		margin-bottom: 12px;
	}
	.loading {
		color: var(--text-2);
	}
	h2.section:first-child {
		margin-top: 4px;
	}
	.ways {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 10px;
	}
	@media (min-width: 900px) {
		.ways {
			grid-template-columns: repeat(4, minmax(0, 1fr));
		}
	}
	.way {
		display: grid;
		gap: 10px;
		align-content: start;
		justify-items: start;
		text-align: left;
		padding: 14px;
		cursor: pointer;
		font: inherit;
		color: var(--text);
		min-height: 120px;
		transition: transform 200ms cubic-bezier(0.3, 1.6, 0.5, 1);
	}
	.way:active {
		transform: scale(0.98);
	}
	.way:hover .wicon {
		background: var(--accent);
		color: var(--on-accent);
	}
	.wicon {
		width: 40px;
		height: 40px;
		border-radius: 13px;
		display: grid;
		place-items: center;
		background: var(--accent-soft);
		color: var(--accent-ink);
		transition: background-color var(--ease);
	}
	.wicon svg {
		fill: none;
		stroke: currentColor;
		stroke-width: 1.8;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.wtext {
		display: grid;
		gap: 2px;
	}
	.wtext strong {
		font-size: 1rem;
	}
	.wtext span {
		font-size: 0.84rem;
		color: var(--text-2);
	}
	.group {
		margin-bottom: 12px;
		padding-top: 12px;
		padding-bottom: 6px;
	}
	.group h3 {
		margin: 0 0 4px;
		font-size: 0.78rem;
		font-weight: 600;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--text-3);
	}
	.row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		align-items: center;
		gap: 2px 8px;
		padding: 4px 0 !important;
	}
	.row.dim .open {
		opacity: 0.65;
	}
	.open {
		display: grid;
		gap: 2px;
		min-width: 0;
		min-height: 52px;
		padding: 6px 4px;
		border: 0;
		border-radius: 12px;
		background: transparent;
		text-align: left;
		cursor: pointer;
		font: inherit;
		color: var(--text);
	}
	.open:hover {
		background: var(--hover);
	}
	.name {
		font-weight: 600;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 8px;
	}
	.small {
		font-size: 0.82rem;
	}
	.url {
		overflow-wrap: anywhere;
	}
	.job {
		grid-column: 1 / -1;
		padding: 0 4px 6px;
		color: var(--accent-ink);
	}
	.job.bad {
		color: var(--warn-ink);
	}
	.job a {
		margin-left: 4px;
	}
	.empty {
		margin: 0 0 8px;
	}
	.formcard {
		max-width: 720px;
	}
	.formcard h2 {
		font-size: 1.25rem;
	}
	.intro {
		margin: -4px 0 16px;
		font-size: 0.92rem;
	}
	.formerr {
		margin-top: 12px;
	}
	.settings {
		margin-top: 8px;
	}
	.settings > summary {
		cursor: pointer;
		min-height: 36px;
		display: flex;
		align-items: center;
		font-weight: 600;
	}
	.settings[open] > summary {
		margin-bottom: 10px;
	}
	.setnote {
		margin-top: 12px;
	}
	.limits {
		margin: 14px 0 0;
	}
</style>
