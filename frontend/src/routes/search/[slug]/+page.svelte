<!-- One flexible search: a calendar heat map per destination (cheapest outlined, ★ under
     target, ↓ at its low, estimates dashed), the same dates as a table cheapest first,
     and tapping a date shows its price history. -->
<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { tick } from 'svelte';
	import { getHistory, getSearch } from '../../../lib/api';
	import { app } from '../../../lib/app.svelte';
	import {
		ago,
		dayDate,
		duration,
		monthDay,
		money,
		nights,
		stops,
		tripDates,
		weekdays
	} from '../../../lib/format';
	import { flagWords } from '../../../lib/heat';
	import { Resource } from '../../../lib/resource.svelte';
	import type { DatePairOut, HistoryOut, SearchOut } from '../../../lib/types.gen';
	import PriceChart from '../../../lib/charts/PriceChart.svelte';
	import ErrorNote from '../../../lib/components/ErrorNote.svelte';
	import Loading from '../../../lib/components/Loading.svelte';
	import PriceCalendar from '../../../lib/components/PriceCalendar.svelte';

	const search = new Resource<SearchOut>();
	const history = new Resource<HistoryOut>();
	const slug = $derived(page.params.slug ?? '');
	let picked = $state<DatePairOut | null>(null);
	let panel = $state<HTMLElement | null>(null);

	$effect(() => {
		search.load(getSearch(slug));
		picked = null;
	});

	const cur = $derived(app.currency);
	const fmt = (c: number | null | undefined) => money(c, cur);

	async function pick(p: DatePairOut) {
		if (picked?.query_id === p.query_id) {
			picked = null;
			return;
		}
		picked = p;
		history.load(getHistory(p.query_id));
		await tick();
		panel?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
		panel?.focus({ preventScroll: true });
	}

	const byPrice = (pairs: DatePairOut[]) =>
		[...pairs].sort(
			(a, b) =>
				(a.price ?? Number.POSITIVE_INFINITY) - (b.price ?? Number.POSITIVE_INFINITY) ||
				a.depart_date.localeCompare(b.depart_date)
		);
	const windowText = (s: SearchOut) =>
		s.window_start && s.window_end
			? `${monthDay(s.window_start)} – ${monthDay(s.window_end)}`
			: 'rolling dates';
</script>

<svelte:head><title>{search.data?.name ?? 'Search'} · Flight Watch</title></svelte:head>

{#snippet detail(p: DatePairOut, dest: string)}
	<section
		class="card detail"
		bind:this={panel}
		tabindex="-1"
		aria-labelledby="detail-h"
		data-testid="date-detail"
	>
		<div class="dhead">
			<div>
				<h3 id="detail-h">
					{p.return_date
						? tripDates(p.depart_date, p.return_date)
						: `${dayDate(p.depart_date)}, one way`}
				</h3>
				<p class="faint">
					{p.origin} → {dest}
					{#if p.price !== null}
						· {p.source === 'calendar' ? 'estimate' : 'checked'} {ago(p.price_at)}
					{/if}
				</p>
			</div>
			<button class="btn small" onclick={() => (picked = null)} aria-label="Close this date"
				>Close</button
			>
		</div>
		<div class="dstats">
			<span class="dprice num">{fmt(p.price)}</span>
			{#if p.source === 'calendar'}<span class="pill neutral">calendar estimate</span>{/if}
			{#each flagWords(p.flags.filter((f) => f !== 'estimate')) as w (w)}<span class="pill good"
					>{w}</span
				>{/each}
		</div>
		{#if p.airline || p.stops !== null}
			<p class="muted small">
				{p.airline ?? ''}{p.stops !== null ? ` · ${stops(p.stops)}` : ''}{p.duration_min
					? ` · ${duration(p.duration_min)}`
					: ''}
			</p>
		{/if}
		{#if history.data && history.data.query_id === p.query_id}
			<PriceChart
				points={history.data.points}
				calendar={history.data.calendar}
				target={history.data.target_price}
				currency={cur}
				height={180}
			/>
		{:else if history.error}
			<p class="muted">Couldn’t load this date’s history.</p>
		{:else}
			<Loading />
		{/if}
		{#if p.link}
			<p class="go">
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an outside link -->
				<a class="btn primary" href={p.link} target="_blank" rel="noopener noreferrer"
					>Open in Google Flights</a
				>
			</p>
		{/if}
	</section>
{/snippet}

{#if search.data}
	{@const s = search.data}
	<header class="pagehead">
		<p class="crumb"><a href={resolve('/')}>Overview</a></p>
		<h1>{s.name}</h1>
		<p>
			{s.origin} → {s.destinations.join(', ')} · {weekdays(s.depart_dows)} · {nights(s.nights)} ·
			{windowText(s)}{s.target_price ? ` · target ${fmt(s.target_price)}` : ''}
		</p>
	</header>

	{#if s.status !== 'active'}
		<p class="card notice">
			This search is <strong>{s.status}</strong>: its dates are no longer checked.
		</p>
	{/if}

	{#if !s.pairs}
		<div class="card">
			<p class="muted">No dates to show: this search has no date pairs in its window.</p>
		</div>
	{:else if !s.priced}
		<div class="card">
			<p class="muted">
				No prices yet. The first ones arrive within a few minutes of adding a search.
			</p>
		</div>
	{/if}

	{#each s.grid as d (d.code)}
		<section class="card dest" aria-labelledby="dest-{d.code}">
			<h2 id="dest-{d.code}">
				{d.label}
				<span class="faint code">{d.code}</span>
				<span class="faint count"
					>{d.pairs.filter((p) => p.price !== null).length} of {d.pairs.length} priced</span
				>
			</h2>
			<PriceCalendar
				pairs={d.pairs}
				currency={cur}
				dest={d.code}
				selected={picked?.query_id ?? null}
				onselect={pick}
			/>
			<p class="legend">
				<span class="li"
					><span class="ramp" aria-hidden="true"
						><i class="r1"></i><i class="r2"></i><i class="r3"></i><i class="r4"></i></span
					>cheaper → dearer</span
				>
				<span class="li"><span class="ring" aria-hidden="true"></span>cheapest</span>
				<span class="li">★ under target</span>
				<span class="li">↓ at its 30-day low</span>
				<span class="li"><span class="dash" aria-hidden="true"></span>calendar estimate</span>
			</p>
			{#if picked && d.pairs.some((p) => p.query_id === picked?.query_id)}
				{@render detail(picked, d.code)}
			{/if}
			<details class="twin">
				<summary>All {d.pairs.length} dates as a table, cheapest first</summary>
				<div class="table-wrap">
					<table class="data">
						<thead>
							<tr>
								<th scope="col">Depart</th>
								{#if s.nights}<th scope="col">Return</th>{/if}
								<th scope="col">Price</th>
								<th scope="col">30-day low</th>
								<th scope="col" class="l">Notes</th>
								<th scope="col"><span class="sr-only">History</span></th>
							</tr>
						</thead>
						<tbody>
							{#each byPrice(d.pairs) as p (p.query_id)}
								<tr>
									<td>{dayDate(p.depart_date)}</td>
									{#if s.nights}<td>{p.return_date ? dayDate(p.return_date) : '–'}</td>{/if}
									<td class="num price" class:est={p.source === 'calendar'}
										>{p.source === 'calendar' ? '≈ ' : ''}{fmt(p.price)}</td
									>
									<td class="num">{fmt(p.low_30d)}</td>
									<td class="l">{flagWords(p.flags).join(', ')}</td>
									<td
										><button class="btn small" onclick={() => pick(p)}
											>{picked?.query_id === p.query_id ? 'Hide' : 'History'}</button
										></td
									>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</details>
		</section>
	{/each}
{:else if search.error}
	<ErrorNote
		error={search.error}
		notFound="No search here"
		retry={() => search.load(getSearch(slug))}
	/>
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
	.dest + .dest {
		margin-top: 14px;
	}
	.dest h2 {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 2px 8px;
		font-size: 1.1rem;
	}
	.code {
		font-weight: 500;
	}
	.count {
		margin-left: auto;
		font-size: 0.8rem;
		font-weight: 500;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 6px;
		margin: 12px 0 0;
		font-size: 0.78rem;
		color: var(--text-2);
	}
	.li {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		margin-right: 10px;
		white-space: nowrap;
	}
	.ramp {
		display: inline-flex;
		gap: 2px;
	}
	.ramp i {
		width: 14px;
		height: 10px;
		border-radius: 3px;
	}
	.r1 {
		background: var(--p1);
	}
	.r2 {
		background: var(--p2);
	}
	.r3 {
		background: var(--p3);
	}
	.r4 {
		background: var(--p4);
	}
	.ring {
		display: inline-block;
		width: 12px;
		height: 12px;
		border-radius: 4px;
		box-shadow: 0 0 0 2px var(--text);
	}
	.dash {
		display: inline-block;
		width: 12px;
		height: 12px;
		border-radius: 4px;
		border: 1.5px dashed var(--estimate);
	}
	.detail {
		margin-top: 14px;
		background: var(--surface);
		box-shadow: none;
		border: 1px solid var(--border);
	}
	.dhead {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 10px;
	}
	.dhead h3 {
		margin: 0;
		font-size: 1rem;
	}
	.dhead p {
		margin: 2px 0 0;
		font-size: 0.82rem;
	}
	.dstats {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px 8px;
		margin: 10px 0 4px;
	}
	.dprice {
		font-size: 1.8rem;
		font-weight: 700;
		letter-spacing: -0.02em;
		margin-right: 4px;
	}
	.small {
		font-size: 0.85rem;
		margin: 0 0 8px;
	}
	td.est {
		font-style: italic;
	}
	.go {
		margin: 12px 0 0;
	}
</style>
