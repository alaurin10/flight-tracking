<!--
  Requests per day, stacked by outcome, for the status page: ok, no flights, blocked,
  and errors (network, unreadable page, other). Pointer or arrow keys pick a day; the
  table underneath has every outcome by name.
-->
<script lang="ts">
	import { scaleBand, scaleLinear } from 'd3-scale';
	import type { RequestDay } from '../types.gen';
	import { monthDay, outcomeLabel } from '../format';
	import Frame from './Frame.svelte';
	import { roundedTopBar } from './geom';

	interface Props {
		days: RequestDay[];
		/** The daily request budget, drawn as a line when it fits the scale. */
		budget?: number | null;
		width?: number;
		height?: number;
	}

	let { days, budget = null, width: fixedWidth, height = 170 }: Props = $props();
	let measured = $state(0);
	let hover = $state<number | null>(null);

	const GROUPS = [
		{ key: 'ok', label: 'ok', match: (o: string) => o === 'ok' },
		{ key: 'empty', label: 'no flights', match: (o: string) => o === 'empty' },
		{ key: 'blocked', label: 'blocked', match: (o: string) => o === 'blocked' },
		{
			key: 'other',
			label: 'errors',
			match: (o: string) => !['ok', 'empty', 'blocked'].includes(o)
		}
	] as const;

	const W = $derived(fixedWidth ?? measured);
	const M = { top: 10, right: 8, bottom: 22, left: 34 };
	const iw = $derived(Math.max(0, W - M.left - M.right));
	const ih = $derived(Math.max(0, height - M.top - M.bottom));
	const totals = $derived(days.map((d) => Object.values(d.outcomes).reduce((a, b) => a + b, 0)));
	const stacks = $derived(
		days.map((d) =>
			GROUPS.map((g) =>
				Object.entries(d.outcomes)
					.filter(([o]) => g.match(o))
					.reduce((a, [, n]) => a + n, 0)
			)
		)
	);
	const outcomes = $derived([...new Set(days.flatMap((d) => Object.keys(d.outcomes)))].sort());
	const x = $derived(
		scaleBand<number>()
			.domain(days.map((_, i) => i))
			.range([0, iw])
			.paddingInner(0.25)
			.paddingOuter(0.1)
	);
	const y = $derived(
		scaleLinear()
			.domain([0, Math.max(5, ...totals, budget ?? 0)])
			.range([ih, 0])
			.nice(4)
	);

	function words(i: number): string {
		const d = days[i];
		const parts = Object.entries(d.outcomes)
			.sort((a, b) => b[1] - a[1])
			.map(([o, n]) => `${n} ${outcomeLabel(o)}`);
		return `${monthDay(d.day)}: ${parts.join(', ') || 'none'}`;
	}

	const readout = $derived(
		hover !== null && days[hover]
			? words(hover)
			: days.length
				? `today ${totals[totals.length - 1]}`
				: ''
	);

	function onpointer(px: number) {
		const step = x.step();
		if (!step || !days.length) return;
		hover = Math.max(0, Math.min(days.length - 1, Math.floor((px - x(0)! + step * 0.125) / step)));
	}

	function onkey(e: KeyboardEvent) {
		if (!days.length) return;
		const n = days.length;
		const at = hover ?? n - 1;
		const next =
			e.key === 'ArrowLeft'
				? Math.max(0, at - 1)
				: e.key === 'ArrowRight'
					? Math.min(n - 1, at + 1)
					: e.key === 'Home'
						? 0
						: e.key === 'End'
							? n - 1
							: null;
		if (e.key === 'Escape') hover = null;
		if (next === null) return;
		e.preventDefault();
		hover = next;
	}
</script>

<div bind:clientWidth={measured}>
	<Frame
		title="Requests per day"
		{readout}
		width={W}
		{height}
		margin={M}
		{y}
		yTicks={y.ticks(4)}
		crossX={hover !== null ? (x(hover) ?? 0) + x.bandwidth() / 2 : null}
		{onpointer}
		onleave={() => (hover = null)}
		{onkey}
		label="Requests to Google per day for the last {days.length} days, by outcome. Use the arrow keys to read each day."
	>
		{#snippet children({ iw })}
			{#each stacks as s, i (i)}
				{@const bx = x(i) ?? 0}
				{#each GROUPS as g, k (g.key)}
					{@const below = s.slice(0, k).reduce((a, b) => a + b, 0)}
					{#if s[k] > 0}
						{@const top = s.slice(0, k + 1).reduce((a, b) => a + b, 0)}
						{@const isTop = s.slice(k + 1).every((v) => v === 0)}
						{#if isTop}
							<path
								class="mark bar {g.key}"
								d={roundedTopBar(bx, y(top), x.bandwidth(), y(below) - y(top))}
							/>
						{:else}
							<rect
								class="mark bar {g.key}"
								x={bx}
								y={y(top)}
								width={x.bandwidth()}
								height={y(below) - y(top)}
							/>
						{/if}
					{/if}
				{/each}
			{/each}
			{#if budget && budget <= y.domain()[1]}
				<line class="mark budget" x1="0" x2={iw} y1={y(budget)} y2={y(budget)} />
			{/if}
		{/snippet}
		{#snippet below({ ih })}
			{#if days.length}
				<text class="xt" x={x(0) ?? 0} y={ih + 16}>{monthDay(days[0].day)}</text>
				<text class="xt" x={(x(days.length - 1) ?? 0) + x.bandwidth()} y={ih + 16} text-anchor="end"
					>{monthDay(days[days.length - 1].day)}</text
				>
			{/if}
		{/snippet}
	</Frame>
	<p class="legend">
		{#each GROUPS as g (g.key)}<span class="li"><span class="k {g.key}"></span>{g.label}</span
			>{/each}
		{#if budget}<span class="li"><span class="k budget"></span>daily budget</span>{/if}
	</p>
	<details class="twin">
		<summary>As a table</summary>
		<div class="table-wrap">
			<table class="data">
				<thead>
					<tr>
						<th scope="col">Day</th>
						{#each outcomes as o (o)}<th scope="col">{outcomeLabel(o)}</th>{/each}
						<th scope="col">Total</th>
					</tr>
				</thead>
				<tbody>
					{#each days as d, i (d.day)}
						<tr>
							<td>{monthDay(d.day)}</td>
							{#each outcomes as o (o)}<td class="num">{d.outcomes[o] ?? 0}</td>{/each}
							<td class="num">{totals[i]}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
</div>

<style>
	.bar.ok {
		fill: var(--o-ok);
	}
	.bar.empty {
		fill: var(--o-empty);
	}
	.bar.blocked {
		fill: var(--o-blocked);
	}
	.bar.other {
		fill: var(--o-other);
	}
	.budget {
		stroke: var(--text-2);
		stroke-width: 1.2;
		stroke-dasharray: 4 3;
	}
	.xt {
		fill: var(--text-3);
		font-size: 10.5px;
	}
	.legend {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 6px;
		margin: 6px 0 0;
		font-size: 0.78rem;
		color: var(--text-2);
	}
	.li {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		margin-right: 8px;
		white-space: nowrap;
	}
	.k {
		display: inline-block;
		width: 10px;
		height: 10px;
		border-radius: 3px;
	}
	.k.ok {
		background: var(--o-ok);
	}
	.k.empty {
		background: var(--o-empty);
	}
	.k.blocked {
		background: var(--o-blocked);
	}
	.k.other {
		background: var(--o-other);
	}
	.k.budget {
		height: 0;
		border-top: 1.5px dashed var(--text-2);
		border-radius: 0;
		width: 14px;
	}
</style>
