<!-- Overview: every trip (price now, the week's change, the verdict, target and low, a
     small chart), the best date of each flexible search, recent alerts and deals, and
     the collector's health. -->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { getOverview } from '../lib/api';
	import { app } from '../lib/app.svelte';
	import { ago, change, dayDate, money, nights, tripDates, weekdays } from '../lib/format';
	import { Resource } from '../lib/resource.svelte';
	import type { OverviewOut } from '../lib/types.gen';
	import Sparkline from '../lib/charts/Sparkline.svelte';
	import ErrorNote from '../lib/components/ErrorNote.svelte';
	import HealthPill from '../lib/components/HealthPill.svelte';
	import Loading from '../lib/components/Loading.svelte';
	import VerdictPill from '../lib/components/VerdictPill.svelte';

	const ov = new Resource<OverviewOut>();
	const load = (fresh = false) => ov.load(getOverview({ fresh }), { keep: fresh });
	$effect(() => load());
	// An open tab stays current: the overview again every five minutes.
	$effect(() => {
		const t = setInterval(() => load(true), 5 * 60 * 1000);
		return () => clearInterval(t);
	});

	const cur = $derived(app.currency);
	const fmt = (c: number | null | undefined) => money(c, cur);
	const NOTE_KIND: Record<string, string> = { price: 'Price', deal: 'Deal', health: 'Health' };
	/** A notification's link, if it points inside the app. */
	const inApp = (url: string | null) => (url && url.startsWith('/') ? url : null);
</script>

<svelte:head><title>Flight Watch</title></svelte:head>

<header class="pagehead">
	<h1>Flight Watch</h1>
	<p class="sub">
		{#if ov.data}
			<HealthPill health={ov.data.health} />
			<span class="faint"
				>{ov.data.health.last_success_at
					? `prices checked ${ago(ov.data.health.last_success_at)}`
					: 'no prices checked yet'}</span
			>
		{:else}
			Flight prices, checked through the day.
		{/if}
	</p>
</header>

{#if ov.data}
	{@const o = ov.data}
	{#if !o.trips.length && !o.searches.length}
		<div class="card empty">
			<h2>Nothing is tracked yet</h2>
			<p class="muted">
				No prices yet. The first ones arrive within a few minutes of adding a trip.
			</p>
		</div>
	{/if}

	{#if o.trips.length}
		<h2 class="section">Trips <span class="note">fixed dates: should you book?</span></h2>
		<div class="trips">
			{#each o.trips as t (t.slug)}
				{@const ch = change(t.change_7d_pct)}
				<a class="card trip" href={resolve('/trip/[slug]', { slug: t.slug })} data-testid="trip">
					<div class="thead">
						<div class="tname">
							<h3>{t.name}</h3>
							<p class="faint">
								{t.origin} → {t.destination}{t.label && t.label !== t.name ? ` · ${t.label}` : ''}
							</p>
							<p class="faint">
								{tripDates(t.depart_date, t.return_date)} · {t.days_to_departure} days out
							</p>
						</div>
						{#if t.status !== 'active'}
							<span class="pill neutral">{t.status}</span>
						{:else}
							<VerdictPill verdict={t.verdict} />
						{/if}
					</div>
					{#if t.price === null}
						<p class="noprice muted">
							No prices yet. The first ones arrive within a few minutes of adding a trip.
						</p>
					{:else}
						<div class="hero">
							<span class="price num">{fmt(t.price)}</span>
							<span class="chg {ch.dir}" title="change over the last 7 days"
								>{ch.text} <span class="faint">this week</span></span
							>
						</div>
						<div class="mini">
							<div>
								<span class="eyebrow">Target</span>
								<span class="num">{fmt(t.target_price)}</span>
							</div>
							<div>
								<span class="eyebrow">All-time low</span>
								<span class="num">{fmt(t.all_time_low)}</span>
							</div>
							<div>
								<span class="eyebrow">Checked</span>
								<span>{ago(t.price_at)}</span>
							</div>
						</div>
						<Sparkline points={t.series} target={t.target_price} currency={cur} />
					{/if}
				</a>
			{/each}
		</div>
	{/if}

	{#if o.searches.length}
		<h2 class="section">
			Flexible searches <span class="note">the cheapest date of each, right now</span>
		</h2>
		<div class="searches">
			{#each o.searches as s (s.slug)}
				<a
					class="card search"
					href={resolve('/search/[slug]', { slug: s.slug })}
					data-testid="search"
				>
					<div class="shead">
						<h3>{s.name}</h3>
						<p class="faint">
							{s.origin} → {s.destinations.join(', ')} · {weekdays(s.depart_dows)} · {nights(
								s.nights
							)}
						</p>
					</div>
					{#if s.best && s.best.price !== null}
						<div class="hero">
							<span class="price num">{fmt(s.best.price)}</span>
							{#if s.best.source === 'calendar'}<span
									class="pill neutral"
									title="from the price calendar, not a checked fare">estimate</span
								>{/if}
							{#if s.best.flags.includes('under_target')}<span class="pill good"
									>★ under target</span
								>{/if}
						</div>
						<p class="muted best">
							{s.destinations.length > 1 ? `${s.best.destination} · ` : ''}{s.best.return_date
								? tripDates(s.best.depart_date, s.best.return_date)
								: dayDate(s.best.depart_date)}
						</p>
						<p class="faint small">{s.priced} of {s.pairs} dates priced</p>
					{:else}
						<p class="noprice muted">
							No prices yet. The first ones arrive within a few minutes of adding a search.
						</p>
					{/if}
				</a>
			{/each}
		</div>
	{/if}

	<div class="lower">
		<section class="card" aria-labelledby="alerts-h">
			<h2 id="alerts-h">Recent alerts</h2>
			{#if o.notifications.length}
				<ul class="rows">
					{#each o.notifications as n (n.id)}
						{@const href = inApp(n.url)}
						<li class="note-row">
							<span class="pill {n.kind === 'health' ? 'warn' : 'neutral'}"
								>{NOTE_KIND[n.kind] ?? n.kind}</span
							>
							<div class="ntext">
								{#if href}
									<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- a path the server wrote, inside this app -->
									<a {href}>{n.title}</a>
								{:else}
									<span>{n.title}</span>
								{/if}
								<p class="faint">{n.body}</p>
							</div>
							<span class="when faint">{ago(n.created_at)}</span>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="muted">
					No alerts yet. One is sent when a price drops below a target or to a new low.
				</p>
			{/if}
		</section>

		<section class="card" aria-labelledby="deals-h">
			<h2 id="deals-h">
				Recent deals <a class="more" href={resolve('/deals')}>All deals</a>
			</h2>
			{#if o.deals.length}
				<ul class="rows">
					{#each o.deals.slice(0, 5) as d (d.id)}
						<li class="deal-row">
							<span class="dprice num">{fmt(d.price)}</span>
							<div class="ntext">
								{#if d.link}
									<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an outside link -->
									<a href={d.link} target="_blank" rel="noopener noreferrer">{d.title}</a>
								{:else}
									<span>{d.title}</span>
								{/if}
								<p class="faint">{d.watch ?? 'no watch'} · {ago(d.published_at ?? d.seen_at)}</p>
							</div>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="muted">No deal posts have matched a watch yet.</p>
			{/if}
		</section>
	</div>
{:else if ov.error}
	<ErrorNote error={ov.error} retry={() => load(true)} />
{:else}
	<Loading />
{/if}

<style>
	.sub {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 10px;
	}
	.trips,
	.searches {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr));
		gap: 14px;
	}
	.trip,
	.search {
		display: grid;
		align-content: start;
		gap: 10px;
		color: inherit;
		text-decoration: none;
		transition: transform var(--ease);
	}
	.trip:hover,
	.search:hover {
		transform: translateY(-1px);
	}
	.thead {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 10px;
	}
	.tname {
		min-width: 0;
	}
	h3 {
		margin: 0 0 2px;
		font-size: 1.05rem;
		font-weight: 650;
	}
	.thead p,
	.shead p {
		margin: 0;
		font-size: 0.82rem;
	}
	.hero {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 4px 12px;
	}
	.price {
		font-size: 2.1rem;
		font-weight: 700;
		letter-spacing: -0.03em;
		line-height: 1;
	}
	.chg {
		font-weight: 600;
		font-size: 0.9rem;
	}
	.chg.down {
		color: var(--down);
	}
	.chg.up {
		color: var(--up);
	}
	.chg .faint {
		font-weight: 500;
	}
	.mini {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 6px;
	}
	.mini > div {
		display: grid;
		gap: 1px;
		min-width: 0;
		font-size: 0.92rem;
		font-weight: 600;
	}
	.mini .eyebrow {
		font-size: 0.66rem;
	}
	.noprice {
		margin: 0;
		font-size: 0.9rem;
	}
	.best {
		margin: 0;
		font-size: 0.9rem;
	}
	.small {
		margin: 0;
		font-size: 0.78rem;
	}
	.lower {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr));
		gap: 14px;
		margin-top: 26px;
	}
	.lower h2 {
		display: flex;
		align-items: baseline;
	}
	.lower h2 .more {
		margin-left: auto;
		font-size: 0.85rem;
		font-weight: 500;
	}
	.note-row,
	.deal-row {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: start;
		gap: 10px;
	}
	.deal-row {
		grid-template-columns: 4.2rem minmax(0, 1fr);
	}
	.ntext {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.ntext p {
		margin: 2px 0 0;
		font-size: 0.8rem;
	}
	.when {
		font-size: 0.78rem;
		white-space: nowrap;
	}
	.dprice {
		font-weight: 700;
		font-size: 1.05rem;
	}
	.empty h2 {
		font-size: 1.1rem;
	}
	.empty p {
		margin: 0;
	}
</style>
