<!-- One trip: the price history with the target drawn on it, the verdict and its
     reasons, the latest offers, the alerts sent, and the way to Google Flights. -->
<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { getTrip } from '../../../lib/api';
	import { app } from '../../../lib/app.svelte';
	import {
		ago,
		alertReason,
		change,
		clock,
		duration,
		instant,
		money,
		stops,
		tripDates
	} from '../../../lib/format';
	import { Resource } from '../../../lib/resource.svelte';
	import type { TripOut } from '../../../lib/types.gen';
	import PriceChart from '../../../lib/charts/PriceChart.svelte';
	import ErrorNote from '../../../lib/components/ErrorNote.svelte';
	import Loading from '../../../lib/components/Loading.svelte';
	import VerdictPill from '../../../lib/components/VerdictPill.svelte';

	const trip = new Resource<TripOut>();
	const slug = $derived(page.params.slug ?? '');
	$effect(() => trip.load(getTrip(slug)));

	const cur = $derived(app.currency);
	const fmt = (c: number | null | undefined) => money(c, cur);
	/** Hours after which a price reads as stale on this page. */
	const STALE_H = 36;
	const staleH = (iso: string | null) =>
		iso ? (Date.now() - Date.parse(iso)) / 3_600_000 : Number.POSITIVE_INFINITY;
</script>

<svelte:head><title>{trip.data?.name ?? 'Trip'} · Flight Watch</title></svelte:head>

{#if trip.data}
	{@const t = trip.data}
	{@const ch = change(t.change_7d_pct)}
	<header class="pagehead">
		<p class="crumb"><a href={resolve('/')}>Overview</a></p>
		<h1>{t.name}</h1>
		<p>
			{t.origin} → {t.destination}{t.label && t.label !== t.name ? ` · ${t.label}` : ''} ·
			{tripDates(t.depart_date, t.return_date)} · {t.days_to_departure} days out
		</p>
	</header>

	{#if t.status !== 'active'}
		<p class="card notice">
			This trip is <strong>{t.status}</strong>: its prices are no longer checked, and its history
			stays here.
		</p>
	{/if}

	<div class="grid">
		<section class="card verdict" aria-labelledby="now-h">
			<h2 id="now-h" class="sr-only">Price now</h2>
			{#if t.price === null}
				<p class="muted">
					No prices yet. The first ones arrive within a few minutes of adding a trip.
				</p>
			{:else}
				<div class="hero">
					<span class="price num" data-testid="price-now">{fmt(t.price)}</span>
					<VerdictPill verdict={t.verdict} />
				</div>
				<p class="checked faint" class:stale={staleH(t.price_at) > STALE_H}>
					<span class="chg {ch.dir}">{ch.text}</span> this week · checked {ago(t.price_at)}
					{#if staleH(t.price_at) > STALE_H}· older than usual{/if}
				</p>
				{#if t.advice?.reasons.length}
					<ul class="reasons">
						{#each t.advice.reasons as r, i (i)}<li>{r}</li>{/each}
					</ul>
				{/if}
				<div class="stats">
					<div class="stat">
						<span class="eyebrow">Target</span><span class="v num">{fmt(t.target_price)}</span>
					</div>
					<div class="stat">
						<span class="eyebrow">All-time low</span><span class="v num">{fmt(t.all_time_low)}</span
						>
					</div>
					<div class="stat">
						<span class="eyebrow">30-day low</span><span class="v num">{fmt(t.low_30d)}</span>
					</div>
					{#if t.advice?.route_p50}
						<div class="stat">
							<span class="eyebrow">Typical</span><span class="v num"
								>{fmt(t.advice.route_p50)}</span
							>
							<span class="sub">median for the route</span>
						</div>
					{/if}
					<div class="stat">
						<span class="eyebrow">Checks</span><span class="v num">{t.observations}</span>
					</div>
				</div>
			{/if}
			{#if t.link}
				<p class="go">
					<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an outside link -->
					<a class="btn primary" href={t.link} target="_blank" rel="noopener noreferrer"
						>Open in Google Flights</a
					>
				</p>
			{/if}
		</section>

		<section class="card chart" aria-labelledby="hist-h">
			<h2 id="hist-h" class="sr-only">Price history</h2>
			<PriceChart points={t.series} target={t.target_price} currency={cur} />
		</section>
	</div>

	<section class="card offers" aria-labelledby="offers-h">
		<h2 id="offers-h">
			Latest offers {#if t.offers_at}<span class="faint small">checked {instant(t.offers_at)}</span
				>{/if}
		</h2>
		{#if t.offers.length}
			<ul class="rows">
				{#each t.offers as o, i (i)}
					<li class="offer">
						<div class="oprice num">{fmt(o.price)}</div>
						<div class="omain">
							<div class="oline">
								<strong>{o.airline ?? 'Airline not given'}</strong>
								<span class="pill neutral">{stops(o.stops)}</span>
							</div>
							<div class="faint">
								{#if o.departs}
									{clock(o.departs)} – {clock(o.arrives, o.departs)} ·
								{/if}
								{duration(o.duration_min)}
								{#if o.layovers.length}
									· {o.layovers
										.map((l) => `${duration(l.minutes)} in ${l.airport ?? '?'}`)
										.join(', ')}
								{/if}
							</div>
						</div>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="muted">No offers stored yet.</p>
		{/if}
	</section>

	<section class="card" aria-labelledby="alerts-h">
		<h2 id="alerts-h">Alerts sent</h2>
		{#if t.alerts.length}
			<ul class="rows">
				{#each t.alerts as a, i (i)}
					<li class="alert">
						<span class="num">{fmt(a.price)}</span>
						<span>{alertReason(a.reason)}</span>
						<span class="faint">{instant(a.sent_at)}</span>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="muted">
				None yet. One is sent when the price drops below the target or to a new low.
			</p>
		{/if}
	</section>

	{#if t.notes}
		<section class="card">
			<h2>Notes</h2>
			<p class="pre">{t.notes}</p>
		</section>
	{/if}
{:else if trip.error}
	<ErrorNote error={trip.error} notFound="No trip here" retry={() => trip.load(getTrip(slug))} />
{:else}
	<Loading />
{/if}

<style>
	.crumb {
		margin: 0 0 6px !important;
		font-size: 0.85rem;
	}
	.notice {
		margin: 0 0 14px;
	}
	.grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 14px;
	}
	@media (min-width: 900px) {
		.grid {
			grid-template-columns: minmax(0, 5fr) minmax(0, 7fr);
		}
	}
	.hero {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px 12px;
	}
	.price {
		font-size: 2.6rem;
		font-weight: 700;
		letter-spacing: -0.03em;
		line-height: 1;
	}
	.checked {
		margin: 8px 0 0;
		font-size: 0.86rem;
	}
	.checked.stale {
		color: var(--warn-ink);
	}
	.chg {
		font-weight: 600;
	}
	.chg.down {
		color: var(--down);
	}
	.chg.up {
		color: var(--up);
	}
	.reasons {
		margin: 12px 0;
		padding-left: 1.2em;
		color: var(--text-2);
	}
	.reasons li + li {
		margin-top: 2px;
	}
	.go {
		margin: 14px 0 0;
	}
	.offers,
	.offers + .card,
	.card + .card {
		margin-top: 14px;
	}
	.small {
		font-size: 0.8rem;
		font-weight: 500;
	}
	.offer {
		display: grid;
		grid-template-columns: 5rem minmax(0, 1fr);
		gap: 10px;
		align-items: start;
	}
	.oprice {
		font-size: 1.15rem;
		font-weight: 700;
	}
	.oline {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 4px 8px;
	}
	.omain .faint {
		font-size: 0.84rem;
		margin-top: 2px;
	}
	.alert {
		display: grid;
		grid-template-columns: 5rem minmax(0, 1fr) auto;
		gap: 10px;
		font-size: 0.9rem;
	}
	.alert .num {
		font-weight: 700;
	}
	.pre {
		white-space: pre-wrap;
		margin: 0;
	}
	@media (max-width: 520px) {
		.alert {
			grid-template-columns: 4.5rem minmax(0, 1fr);
		}
		.alert .faint {
			grid-column: 2;
		}
	}
</style>
