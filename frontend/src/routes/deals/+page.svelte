<!-- Two views. Explore: whole regions priced on Google's Explore map, one board per region,
     airport and trip length, each place at its cheapest current fare over the weeks ahead.
     Posts: new posts from the deal pages and feeds; "Matches only" keeps those that alerted
     for a region, trip or flexible search. -->
<script lang="ts">
	import { getDeals, getExplore } from '../../lib/api';
	import { app } from '../../lib/app.svelte';
	import { ago, duration, instant, money, stops, tripDates } from '../../lib/format';
	import { Resource } from '../../lib/resource.svelte';
	import type { DealOut, ExploreBoardOut } from '../../lib/types.gen';
	import ErrorNote from '../../lib/components/ErrorNote.svelte';
	import Loading from '../../lib/components/Loading.svelte';

	const deals = new Resource<DealOut[]>();
	$effect(() => deals.load(getDeals()));
	const explore = new Resource<ExploreBoardOut[]>();
	$effect(() => explore.load(getExplore()));
	let tab = $state<'explore' | 'posts'>('explore');
	const SHOWN = 10;
	let opened = $state<Record<string, boolean>>({});
	const cur = $derived(app.currency);
	let matchesOnly = $state(false);
	const shown = $derived((deals.data ?? []).filter((d) => !matchesOnly || d.watch));
	const host = (u: string) => {
		try {
			return new URL(u).host.replace(/^www\./, '');
		} catch {
			return u;
		}
	};
</script>

<svelte:head><title>Deals · Flight Watch</title></svelte:head>

<header class="pagehead">
	<h1>Deals</h1>
	<p>
		{tab === 'explore'
			? 'Cheap places on Google’s Explore map, for every week ahead.'
			: 'New posts from your deal pages. Those marked with a name matched something you watch.'}
	</p>
	<div class="seg filter" role="group" aria-label="Which deals">
		<button aria-pressed={tab === 'explore'} onclick={() => (tab = 'explore')}>Explore</button>
		<button aria-pressed={tab === 'posts'} onclick={() => (tab = 'posts')}>Posts</button>
	</div>
	{#if tab === 'posts'}
		<div class="seg filter" role="group" aria-label="Which posts">
			<button aria-pressed={!matchesOnly} onclick={() => (matchesOnly = false)}>All posts</button>
			<button aria-pressed={matchesOnly} onclick={() => (matchesOnly = true)}>Matches only</button>
		</div>
	{/if}
</header>

{#if tab === 'explore'}
	{#if explore.data}
		{#if explore.data.length}
			<p class="faint note">
				Fares are Google’s cheapest found for each date, basic economy included: a guide, not a
				quote. Each board fills in as its weeks are priced again.
			</p>
			{#each explore.data as b (b.key)}
				{@const all = b.places}
				{@const shown = opened[b.key] ? all : all.slice(0, SHOWN)}
				<section class="card board" aria-labelledby="board-{b.key}" data-testid="board">
					<h2 id="board-{b.key}">{b.title}</h2>
					<p class="faint small">
						{b.priced_departures} of {b.departures} weeks priced{b.updated_at
							? ` · updated ${ago(b.updated_at)}`
							: ''}
					</p>
					{#if all.length}
						<ul class="rows places">
							{#each shown as p (p.place_id)}
								<li class="place" data-testid="place">
									<span class="price num">{money(p.price, p.currency)}</span>
									<div class="body">
										<div class="pname">
											{#if p.link}
												<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an outside link -->
												<a href={p.link} target="_blank" rel="noopener noreferrer">{p.name}</a>
											{:else}
												<span>{p.name}</span>
											{/if}
											<span class="faint">{[p.country, p.airport].filter(Boolean).join(' · ')}</span
											>
											{#if p.under_max}<span class="pill good">under your price</span>{/if}
										</div>
										<p class="faint">
											<span>{tripDates(p.depart_date, p.return_date)}</span>
											{#if p.stops !== null}<span>· {stops(p.stops)}</span>{/if}
											{#if p.duration_min}<span>· {duration(p.duration_min)}</span>{/if}
											{#if p.airline}<span>· {p.airline}</span>{/if}
											<span title={instant(p.observed_at)}>· seen {ago(p.observed_at)}</span>
										</p>
									</div>
								</li>
							{/each}
						</ul>
						{#if all.length > SHOWN}
							<button
								class="btn more"
								type="button"
								onclick={() => (opened[b.key] = !opened[b.key])}
								>{opened[b.key] ? 'Show fewer' : `Show all ${all.length}`}</button
							>
						{/if}
					{:else}
						<p class="muted">
							No fares yet. Google returns a few places’ fares per week priced; they appear here as
							they come in.
						</p>
					{/if}
				</section>
			{/each}
		{:else}
			<div class="card">
				<p class="muted">Explore is turned off on the server (explore.enabled).</p>
			</div>
		{/if}
	{:else if explore.error}
		<ErrorNote error={explore.error} retry={() => explore.load(getExplore())} />
	{:else}
		<Loading />
	{/if}
{:else if deals.data}
	{#if shown.length}
		<ul class="card rows deals">
			{#each shown as d (d.id)}
				<li class="deal" data-testid="deal">
					<span class="price num">{money(d.price, cur)}</span>
					<div class="body">
						{#if d.link}
							<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an outside link -->
							<a href={d.link} target="_blank" rel="noopener noreferrer">{d.title}</a>
						{:else}
							<span>{d.title}</span>
						{/if}
						<p class="faint">
							{#if d.watch}<span class="pill good">{d.watch}</span>{/if}
							<span title={instant(d.published_at ?? d.seen_at)}
								>{ago(d.published_at ?? d.seen_at)}</span
							>
							· {host(d.feed)}
						</p>
					</div>
				</li>
			{/each}
		</ul>
	{:else}
		<div class="card">
			<p class="muted">
				{matchesOnly
					? 'No post has matched a region, trip or flexible search yet.'
					: 'No posts yet. Deal pages are read every few hours.'}
			</p>
		</div>
	{/if}
{:else if deals.error}
	<ErrorNote error={deals.error} retry={() => deals.load(getDeals())} />
{:else}
	<Loading />
{/if}

<style>
	.filter {
		margin-top: 12px;
	}
	.filter + .filter {
		margin-top: 8px;
	}
	.note {
		margin: 0 0 12px;
		font-size: 0.85rem;
	}
	.board + .board {
		margin-top: 14px;
	}
	.board h2 {
		margin-bottom: 2px;
	}
	.small {
		font-size: 0.8rem;
		margin: 0 0 10px;
	}
	.places {
		margin: 0;
	}
	.place {
		display: grid;
		grid-template-columns: 4.6rem minmax(0, 1fr);
		gap: 12px;
		align-items: start;
	}
	.pname {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 4px 8px;
	}
	.pname a,
	.pname > span:first-child {
		font-weight: 600;
	}
	.more {
		margin-top: 10px;
	}
	.deals {
		margin: 0;
	}
	.deal {
		display: grid;
		grid-template-columns: 4.6rem minmax(0, 1fr);
		gap: 12px;
		align-items: start;
	}
	.price {
		font-size: 1.2rem;
		font-weight: 700;
	}
	.body {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.body a {
		font-weight: 500;
	}
	.body p {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 8px;
		margin: 4px 0 0;
		font-size: 0.8rem;
	}
</style>
