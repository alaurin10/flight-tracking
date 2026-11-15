<!-- What is tracked, for the owner: trips, flexible searches, deal watches and deal feeds,
     each with its status and edit, check now, pause, resume, archive and delete; and the
     alert settings. Anyone can open it; it asks for the owner password first (the sign-in
     and its session state are copied from Snow Watch's Manage page). Forms open over the
     list with shallow routing, so the Back button closes them. -->
<script lang="ts">
	import { page } from '$app/state';
	import { pushState } from '$app/navigation';
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
		setStatus,
		waitForJob,
		type Status,
		type WatchKind
	} from '../../lib/api';
	import { app } from '../../lib/app.svelte';
	import { friendlyError, localTimes, sentence } from '../../lib/errors';
	import { instant, money, nights, tripDates, weekdays } from '../../lib/format';
	import { owner } from '../../lib/session.svelte';
	import OwnerLogin from '../../lib/manage/OwnerLogin.svelte';
	import TripForm from '../../lib/manage/TripForm.svelte';
	import SearchForm from '../../lib/manage/SearchForm.svelte';
	import DealWatchForm from '../../lib/manage/DealWatchForm.svelte';
	import FeedForm from '../../lib/manage/FeedForm.svelte';
	import AlertSettingsForm from '../../lib/manage/AlertSettingsForm.svelte';
	import ConfirmDialog from '../../lib/manage/ConfirmDialog.svelte';
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

	let trips = $state<Trip[]>([]);
	let searches = $state<Search[]>([]);
	let watches = $state<DealWatch[]>([]);
	let feeds = $state<DealFeed[]>([]);
	let collecting = $state<CollectingOut | null>(null);
	let settings = $state<AlertSettings | null>(null);
	let loaded = $state(false);
	let error = $state<string | null>(null);
	let formError = $state<string | null>(null);
	let saving = $state(false);
	let settingsNote = $state<string | null>(null);
	// Per row ("trip:3"): what its "Check now" or save is doing, and a link once done.
	let progress = $state<Record<string, { text: string; bad?: boolean; href?: string }>>({});
	let asking = $state<{ kind: WatchKind; id: number; name: string } | null>(null);

	const open = $derived(page.state.manage ?? null);
	const home = $derived(app.meta?.home_airport ?? '');
	const leaving = new AbortController();

	async function load() {
		error = null;
		try {
			const me = owner.me ?? (await owner.load());
			if (!me.is_owner) return;
			[trips, searches, watches, feeds, collecting, settings] = await Promise.all([
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

	/** Run an owner action; any failure becomes the page's error message. */
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

	// -- fetch jobs: "check now" and the first price after saving ---------------------------

	const say = (key: string, text: string, extra: { bad?: boolean; href?: string } = {}) =>
		(progress[key] = { text, ...extra });

	async function follow(key: string, jobId: number, name: string, href: string) {
		say(key, `Queued: getting prices for ${name}…`);
		try {
			const job = await waitForJob(
				jobId,
				(j) => {
					if (j.status === 'running') say(key, `Fetching prices for ${name}…`);
				},
				leaving.signal
			);
			if (leaving.signal.aborted) return;
			if (job.status === 'done') say(key, `Done: ${name} has a fresh price.`, { href });
			else if (job.status === 'failed')
				say(key, sentence(localTimes(job.error ?? 'it failed for an unknown reason')), {
					bad: true
				});
			else say(key, `Still waiting for the collector; the price will appear on its page.`);
			forgetOverview();
		} catch (e) {
			if (!leaving.signal.aborted && !owner.ended(e)) say(key, friendlyError(e), { bad: true });
		}
	}

	async function checkNow(kind: 'trip' | 'search', slug: string, id: number, name: string) {
		const key = `${kind}:${id}`;
		const href =
			kind === 'trip' ? resolve('/trip/[slug]', { slug }) : resolve('/search/[slug]', { slug });
		try {
			collecting = await getCollecting();
			if (collecting.cooldown_until && Date.parse(collecting.cooldown_until) > Date.now()) {
				say(
					key,
					`Collecting is paused until ${instant(collecting.cooldown_until)} (Google asked us to slow down). Nothing was queued; ${name} goes first when it resumes.`,
					{ bad: true }
				);
				return;
			}
			const job = await queueJob(
				kind === 'trip'
					? { kind: 'fetch_trip', trip: slug }
					: { kind: 'fetch_search', search: slug }
			);
			void follow(key, job.id, name, href);
		} catch (e) {
			if (owner.ended(e)) return;
			say(key, friendlyError(e), { bad: true });
		}
	}

	// -- saving -----------------------------------------------------------------------------

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
			if (r.job_id)
				void follow(
					`trip:${r.trip.id}`,
					r.job_id,
					r.trip.name,
					resolve('/trip/[slug]', { slug: r.trip.slug })
				);
		});
	const onSearch = (body: SearchIn) =>
		save(async () => {
			const r = await saveSearch(body, open?.id);
			if (r.job_id)
				void follow(
					`search:${r.search.id}`,
					r.job_id,
					r.search.name,
					resolve('/search/[slug]', { slug: r.search.slug })
				);
		});
	const onWatch = (body: DealWatchIn) =>
		save(async () => {
			await saveDealWatch(body, open?.id);
		});
	const onFeed = (body: DealFeedIn) =>
		save(async () => {
			await saveDealFeed(body, open?.id);
		});

	const status = (kind: WatchKind, id: number, to: Status, name: string) =>
		act(async () => {
			const r = (await setStatus(kind, id, to)) as {
				job_id?: number | null;
				trip?: Trip;
				search?: Search;
			};
			await changed();
			if (r.job_id && r.trip)
				void follow(`trip:${id}`, r.job_id, name, resolve('/trip/[slug]', { slug: r.trip.slug }));
			if (r.job_id && r.search)
				void follow(
					`search:${id}`,
					r.job_id,
					name,
					resolve('/search/[slug]', { slug: r.search.slug })
				);
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

	const find = <T extends { id: number }>(list: T[], id?: number) =>
		id === undefined ? undefined : list.find((x) => x.id === id);
	const label = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
	const pillFor = (s: string) => (s === 'active' ? 'good' : s === 'booked' ? 'good' : 'neutral');
	const cooling = $derived(
		collecting?.cooldown_until && Date.parse(collecting.cooldown_until) > Date.now()
			? collecting.cooldown_until
			: null
	);
	const kindName: Record<WatchKind, string> = {
		trips: 'trip',
		searches: 'flexible search',
		'deal-watches': 'deal watch',
		'deal-feeds': 'deal feed'
	};
</script>

<svelte:head><title>Manage · Flight Watch</title></svelte:head>

<header class="pagehead head">
	<div>
		<h1>Manage</h1>
		<p>What Flight Watch tracks.</p>
	</div>
	{#if owner.isOwner}<button class="btn" onclick={signOut}>Sign out</button>{/if}
</header>

{#if error}<p class="notice bad top" role="alert">{error}</p>{/if}

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
			Managing what is tracked is turned off on this server: set <code>OWNER_PASSWORD</code> (12 or
			more characters) to turn it on. <a href={resolve('/')}>Back to the overview</a>
		</p>
	{/if}
{:else if !owner.me || !loaded}
	{#if !error}<p class="card loading" aria-live="polite">Loading…</p>{/if}
{:else if open}
	<section class="card formcard" aria-labelledby="form-h">
		{#if open.form === 'trip'}
			{@const t = find(trips, open.id)}
			<h2 id="form-h">{t ? `Edit ${t.name}` : 'Add a trip'}</h2>
			<p class="muted intro">
				One fixed date pair, priced a few times a day until it departs: “when should I book?”
			</p>
			<TripForm initial={t} {home} {saving} onsave={onTrip} oncancel={closeForm} />
		{:else if open.form === 'search'}
			{@const s = find(searches, open.id)}
			<h2 id="form-h">{s ? `Edit ${s.name}` : 'Add a flexible search'}</h2>
			<p class="muted intro">
				Many dates of one shape, to find the cheapest: “which weekend is cheapest?”
			</p>
			<SearchForm initial={s} {home} {saving} onsave={onSearch} oncancel={closeForm} />
		{:else if open.form === 'deal-watch'}
			{@const w = find(watches, open.id)}
			<h2 id="form-h">{w ? `Edit ${w.name}` : 'Add a deal watch'}</h2>
			<DealWatchForm
				initial={w}
				regions={collecting?.regions ?? []}
				{saving}
				onsave={onWatch}
				oncancel={closeForm}
			/>
		{:else}
			{@const f = find(feeds, open.id)}
			<h2 id="form-h">{f ? 'Edit deal feed' : 'Add a deal feed'}</h2>
			<FeedForm initial={f} {saving} onsave={onFeed} oncancel={closeForm} />
		{/if}
		{#if formError}<p class="notice bad formerr" role="alert">{formError}</p>{/if}
	</section>
{:else}
	{#if collecting}
		<p class="summary muted" data-testid="limits">
			{collecting.active_trips} of {collecting.max_trips} trips · {collecting.active_searches} of
			{collecting.max_searches} searches · {collecting.active_pairs} of {collecting.max_pairs} date pairs
			· {collecting.budget.used} of {collecting.budget.limit} requests used in the last 24 h
		</p>
	{/if}
	{#if cooling}
		<p class="notice caution top" role="status">
			Collecting is paused until {instant(cooling)} because Google asked us to slow down. Changes are
			saved; their prices come when it resumes.
		</p>
	{/if}

	<!-- Trips -->
	<h2 class="section">
		Trips <span class="note">fixed dates</span>
		<button class="btn primary small more" onclick={() => openForm('trip')}>Add trip</button>
	</h2>
	<section class="card">
		{#if trips.length}
			<ul class="rows">
				{#each trips as t (t.id)}
					{@const st = t.status ?? 'active'}
					{@const p = progress[`trip:${t.id}`]}
					<li class="item" class:dim={st !== 'active'} data-testid="manage-trip">
						<div class="info">
							<div class="title">
								<strong>{t.name}</strong>
								<span class="pill {pillFor(st)}">{label(st)}</span>
							</div>
							<span class="faint small">
								{t.origin} → {t.destination} · {tripDates(t.depart_date, t.return_date)}
								{#if t.target_price != null}· target {money(t.target_price, app.currency)}{/if}
							</span>
							{#if p}
								<span class="small job" class:bad={p.bad} role="status">
									{p.text}
									{#if p.href}
										<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve() in follow() -->
										<a href={p.href}>Open it</a>{/if}
								</span>
							{/if}
						</div>
						<div class="btns">
							<button
								class="btn small"
								onclick={() => openForm('trip', t.id)}
								aria-label={`Edit ${t.name}`}>Edit</button
							>
							{#if st === 'active'}
								<button
									class="btn small"
									onclick={() => checkNow('trip', t.slug, t.id, t.name)}
									aria-label={`Check ${t.name} now`}>Check now</button
								>
								<button
									class="btn small"
									onclick={() => status('trips', t.id, 'paused', t.name)}
									aria-label={`Pause ${t.name}`}>Pause</button
								>
								<button
									class="btn small"
									onclick={() => status('trips', t.id, 'booked', t.name)}
									aria-label={`Mark ${t.name} as booked`}>Booked</button
								>
							{:else}
								<button
									class="btn small"
									onclick={() => status('trips', t.id, 'active', t.name)}
									aria-label={`Resume ${t.name}`}>Resume</button
								>
							{/if}
							{#if st !== 'archived'}
								<button
									class="btn small"
									onclick={() => status('trips', t.id, 'archived', t.name)}
									aria-label={`Archive ${t.name}`}>Archive</button
								>
							{/if}
							<button
								class="btn small"
								onclick={() => (asking = { kind: 'trips', id: t.id, name: t.name })}
								aria-label={`Delete ${t.name}`}>Delete</button
							>
						</div>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="muted empty">
				No trips yet. Add one with fixed dates to see when to book; its first price arrives within a
				minute or so.
			</p>
		{/if}
	</section>

	<!-- Flexible searches -->
	<h2 class="section">
		Flexible searches <span class="note">many dates</span>
		<button class="btn primary small more" onclick={() => openForm('search')}>Add search</button>
	</h2>
	<section class="card">
		{#if searches.length}
			<ul class="rows">
				{#each searches as s (s.id)}
					{@const st = s.status ?? 'active'}
					{@const p = progress[`search:${s.id}`]}
					<li class="item" class:dim={st !== 'active'} data-testid="manage-search">
						<div class="info">
							<div class="title">
								<strong>{s.name}</strong>
								<span class="pill {pillFor(st)}">{label(st)}</span>
							</div>
							<span class="faint small">
								{s.origin} → {s.destinations.join(', ')} · {weekdays(s.depart_dows)} · {nights(
									s.nights
								)}
								{#if s.window_start || s.window_end}
									· {s.window_start ?? '…'} to {s.window_end ?? '…'}{:else}· rolling{/if}
								{#if s.target_price != null}· target {money(s.target_price, app.currency)}{/if}
							</span>
							{#if p}
								<span class="small job" class:bad={p.bad} role="status">
									{p.text}
									{#if p.href}
										<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve() in follow() -->
										<a href={p.href}>Open it</a>{/if}
								</span>
							{/if}
						</div>
						<div class="btns">
							<button
								class="btn small"
								onclick={() => openForm('search', s.id)}
								aria-label={`Edit ${s.name}`}>Edit</button
							>
							{#if st === 'active'}
								<button
									class="btn small"
									onclick={() => checkNow('search', s.slug, s.id, s.name)}
									aria-label={`Check ${s.name} now`}>Check now</button
								>
								<button
									class="btn small"
									onclick={() => status('searches', s.id, 'paused', s.name)}
									aria-label={`Pause ${s.name}`}>Pause</button
								>
							{:else}
								<button
									class="btn small"
									onclick={() => status('searches', s.id, 'active', s.name)}
									aria-label={`Resume ${s.name}`}>Resume</button
								>
							{/if}
							{#if st !== 'archived'}
								<button
									class="btn small"
									onclick={() => status('searches', s.id, 'archived', s.name)}
									aria-label={`Archive ${s.name}`}>Archive</button
								>
							{/if}
							<button
								class="btn small"
								onclick={() => (asking = { kind: 'searches', id: s.id, name: s.name })}
								aria-label={`Delete ${s.name}`}>Delete</button
							>
						</div>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="muted empty">
				No flexible searches yet. A search prices every matching date, e.g. Friday-to-Sunday trips
				to two cities, to find the cheapest.
			</p>
		{/if}
	</section>

	<!-- Deal watches -->
	<h2 class="section">
		Deal watches <span class="note">headlines from deal feeds</span>
		<button class="btn primary small more" onclick={() => openForm('deal-watch')}>Add watch</button>
	</h2>
	<section class="card">
		{#if watches.length}
			<ul class="rows">
				{#each watches as w (w.id)}
					{@const st = w.status ?? 'active'}
					<li class="item" class:dim={st !== 'active'} data-testid="manage-watch">
						<div class="info">
							<div class="title">
								<strong>{w.name}</strong>
								<span class="pill {pillFor(st)}">{label(st)}</span>
							</div>
							<span class="faint small">
								from {w.origins?.length ? w.origins.join(', ') : 'anywhere'} · to
								{w.destinations?.length ? w.destinations.join(', ') : 'anywhere'}
								{#if w.max_price != null}· up to {money(w.max_price, app.currency)}{/if}
							</span>
						</div>
						<div class="btns">
							<button
								class="btn small"
								onclick={() => openForm('deal-watch', w.id)}
								aria-label={`Edit ${w.name}`}>Edit</button
							>
							{#if st === 'active'}
								<button
									class="btn small"
									onclick={() => status('deal-watches', w.id, 'paused', w.name)}
									aria-label={`Pause ${w.name}`}>Pause</button
								>
							{:else}
								<button
									class="btn small"
									onclick={() => status('deal-watches', w.id, 'active', w.name)}
									aria-label={`Resume ${w.name}`}>Resume</button
								>
							{/if}
							{#if st !== 'archived'}
								<button
									class="btn small"
									onclick={() => status('deal-watches', w.id, 'archived', w.name)}
									aria-label={`Archive ${w.name}`}>Archive</button
								>
							{/if}
							<button
								class="btn small"
								onclick={() => (asking = { kind: 'deal-watches', id: w.id, name: w.name })}
								aria-label={`Delete ${w.name}`}>Delete</button
							>
						</div>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="muted empty">
				No deal watches yet. A watch alerts when a deal feed posts a fare that matches it, such as
				Seattle to Hawaii under $400.
			</p>
		{/if}
	</section>

	<!-- Deal feeds -->
	<h2 class="section">
		Deal feeds
		<button class="btn primary small more" onclick={() => openForm('feed')}>Add feed</button>
	</h2>
	<section class="card">
		{#if feeds.length}
			<ul class="rows">
				{#each feeds as f (f.id)}
					{@const st = f.status ?? 'active'}
					{@const nm = f.name || f.url}
					<li class="item" class:dim={st !== 'active'} data-testid="manage-feed">
						<div class="info">
							<div class="title">
								<strong class="url">{nm}</strong>
								<span class="pill {pillFor(st)}">{label(st)}</span>
							</div>
							{#if f.name}<span class="faint small url">{f.url}</span>{/if}
						</div>
						<div class="btns">
							<button
								class="btn small"
								onclick={() => openForm('feed', f.id)}
								aria-label={`Edit ${nm}`}>Edit</button
							>
							{#if st === 'active'}
								<button
									class="btn small"
									onclick={() => status('deal-feeds', f.id, 'paused', nm)}
									aria-label={`Pause ${nm}`}>Pause</button
								>
							{:else}
								<button
									class="btn small"
									onclick={() => status('deal-feeds', f.id, 'active', nm)}
									aria-label={`Resume ${nm}`}>Resume</button
								>
							{/if}
							<button
								class="btn small"
								onclick={() => (asking = { kind: 'deal-feeds', id: f.id, name: nm })}
								aria-label={`Delete ${nm}`}>Delete</button
							>
						</div>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="muted empty">
				{#if collecting?.default_feeds.length}
					None added here, so the default feeds are polled:
					{#each collecting.default_feeds as u, i (u)}<span class="url">{u}</span>{i <
						collecting.default_feeds.length - 1
							? ', '
							: '.'}
					{/each}
					Adding one here replaces them.
				{:else}
					No feeds: deal watches have nothing to read until one is added.
				{/if}
			</p>
		{/if}
	</section>

	<!-- Alert settings -->
	<h2 class="section">Alert settings</h2>
	<section class="card">
		{#if settings}
			<AlertSettingsForm initial={settings} {saving} onsave={onSettings} />
			{#if settingsNote}<p class="notice info setnote" role="status">{settingsNote}</p>{/if}
		{/if}
	</section>
{/if}

{#if asking}
	<ConfirmDialog
		title={`Delete ${asking.name}?`}
		body={`The ${kindName[asking.kind]} is removed. Prices already collected are kept, and adding it again picks its history back up. To stop it for now, Pause or Archive instead.`}
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
	.summary {
		margin: 0 4px 4px;
		font-size: 0.86rem;
	}
	.loading {
		color: var(--text-2);
	}
	.formcard {
		max-width: 720px;
	}
	.formcard h2 {
		font-size: 1.2rem;
	}
	.intro {
		margin: -4px 0 14px;
		font-size: 0.9rem;
	}
	.formerr {
		margin-top: 12px;
	}
	.item {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 8px;
	}
	@media (min-width: 900px) {
		.item {
			grid-template-columns: minmax(0, 1fr) auto;
			align-items: center;
		}
	}
	.item.dim .info {
		opacity: 0.7;
	}
	.info {
		display: grid;
		gap: 2px;
		min-width: 0;
	}
	.title {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 4px 8px;
	}
	.small {
		font-size: 0.82rem;
	}
	.url {
		overflow-wrap: anywhere;
	}
	.job {
		color: var(--accent-ink);
	}
	.job.bad {
		color: var(--warn-ink);
	}
	.job a {
		margin-left: 4px;
	}
	.btns {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.btns .btn {
		min-height: 36px;
	}
	.empty {
		margin: 0;
	}
	.setnote {
		margin-top: 12px;
	}
	h2.section .more {
		min-height: 34px;
	}
</style>
