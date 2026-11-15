<!--
  Price history for one date pair: the cheapest fare at each check as a line, calendar
  estimates as hollow dots, and the target as a dashed line (the y scale always
  includes it, so it is drawn inside the plot). Pointer or arrow keys move the
  crosshair; the same numbers are in the table underneath.
-->
<script lang="ts">
	import { scaleLinear } from 'd3-scale';
	import type { PricePoint } from '../types.gen';
	import { instant, instantDay, money } from '../format';
	import Frame from './Frame.svelte';
	import { areaPath, linePath, nearest, priceDomain } from './geom';

	interface Props {
		points: PricePoint[];
		/** Calendar (price graph) estimates for the same dates, if any. */
		calendar?: PricePoint[];
		target?: number | null;
		currency?: string;
		title?: string;
		/** A fixed width (tests); otherwise the chart fills its container. */
		width?: number;
		height?: number;
	}

	let {
		points,
		calendar = [],
		target = null,
		currency = 'USD',
		title = 'Price history',
		width: fixedWidth,
		height = 210
	}: Props = $props();

	let measured = $state(0);
	let hover = $state<number | null>(null);

	const W = $derived(fixedWidth ?? measured);
	const M = { top: 12, right: 12, bottom: 24, left: 52 };
	const iw = $derived(Math.max(0, W - M.left - M.right));
	const ih = $derived(Math.max(0, height - M.top - M.bottom));
	const fmt = (c: number) => money(c, currency);

	const times = $derived(points.map((p) => Date.parse(p.at)));
	const calTimes = $derived(calendar.map((p) => Date.parse(p.at)));
	const domainX = $derived.by((): [number, number] => {
		const all = [...times, ...calTimes];
		if (!all.length) return [0, 1];
		const lo = Math.min(...all);
		const hi = Math.max(...all);
		// One check: a day either side, so the dot sits in the middle.
		return lo === hi ? [lo - 86_400_000, hi + 86_400_000] : [lo, hi];
	});
	const x = $derived(scaleLinear().domain(domainX).range([0, iw]));
	const y = $derived(
		scaleLinear()
			.domain(priceDomain([...points.map((p) => p.price), ...calendar.map((p) => p.price), target]))
			.range([ih, 0])
	);
	const ticks = $derived(y.ticks(4).filter((t) => t >= y.domain()[0] && t <= y.domain()[1]));
	const xs = $derived(times.map((t) => x(t)));
	const ys = $derived(points.map((p) => y(p.price)));
	const last = $derived(points.at(-1));
	const low = $derived(points.length ? Math.min(...points.map((p) => p.price)) : null);

	const readout = $derived.by(() => {
		if (hover !== null && points[hover]) {
			const p = points[hover];
			return `${instantDay(p.at)} · ${fmt(p.price)}`;
		}
		return last ? `now ${fmt(last.price)}` : '';
	});

	function onpointer(px: number) {
		hover = nearest(xs, px);
	}

	function onkey(e: KeyboardEvent) {
		if (!points.length) return;
		const n = points.length;
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

	/** Every point for the table, newest first. */
	const rows = $derived(
		[
			...points.map((p) => ({ ...p, kind: 'price' as const })),
			...calendar.map((p) => ({ ...p, kind: 'estimate' as const }))
		].sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
	);
	const label = $derived(
		points.length
			? `${title}: ${points.length} checks from ${instantDay(points[0].at)} to ${instantDay(points[points.length - 1].at)}, ` +
					`low ${fmt(low ?? 0)}, now ${fmt(last?.price ?? 0)}` +
					(target ? `, target ${fmt(target)}` : '') +
					'. Use the arrow keys to read each check.'
			: `${title}: no prices yet`
	);
</script>

<div class="pricechart" bind:clientWidth={measured}>
	<Frame
		{title}
		{readout}
		width={W}
		{height}
		margin={M}
		{y}
		yTicks={ticks}
		yFormat={fmt}
		crossX={hover !== null ? (xs[hover] ?? null) : null}
		{onpointer}
		onleave={() => (hover = null)}
		{onkey}
		{label}
	>
		{#snippet children({ ih })}
			{#if target !== null && target !== undefined}
				<line class="mark target" x1="0" x2={iw} y1={y(target)} y2={y(target)} />
				<text
					class="tlabel"
					x={iw - 4}
					y={y(target) - 5 < 10 ? y(target) + 13 : y(target) - 5}
					text-anchor="end">target {fmt(target)}</text
				>
			{/if}
			{#if points.length}
				<path class="mark area" d={areaPath(xs, ys, ih)} />
				<path class="mark line" d={linePath(xs, ys)} data-n={points.length} />
			{:else}
				<text class="empty" x="8" y={ih / 2}>No prices yet.</text>
			{/if}
			{#each calendar as c, i (i)}
				<circle class="mark est" cx={x(calTimes[i])} cy={y(c.price)} r="3.5" />
			{/each}
			{#if last}
				<circle class="mark end" cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r="4.5" />
			{/if}
			{#if hover !== null && points[hover]}
				<circle class="mark hov" cx={xs[hover]} cy={ys[hover]} r="5" />
			{/if}
		{/snippet}
		{#snippet below({ ih })}
			{#if points.length || calendar.length}
				<text class="xt" x="0" y={ih + 16}>{instantDay(new Date(domainX[0]).toISOString())}</text>
				<text class="xt" x={iw} y={ih + 16} text-anchor="end"
					>{instantDay(new Date(domainX[1]).toISOString())}</text
				>
			{/if}
		{/snippet}
	</Frame>
	{#if calendar.length}
		<p class="legend">
			<span class="li"><span class="k line"></span>price checked</span>
			<span class="li"><span class="k est"></span>calendar estimate</span>
		</p>
	{/if}
	{#if rows.length}
		<details class="twin">
			<summary>As a table ({rows.length} {rows.length === 1 ? 'row' : 'rows'})</summary>
			<div class="table-wrap">
				<table class="data">
					<thead>
						<tr><th scope="col">Checked</th><th scope="col">Price</th><th scope="col">Kind</th></tr>
					</thead>
					<tbody>
						{#each rows as r, i (i)}
							<tr data-kind={r.kind}>
								<td>{instant(r.at)}</td>
								<td class="num">{fmt(r.price)}</td>
								<td>{r.kind === 'price' ? 'price' : 'estimate'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</details>
	{/if}
</div>

<style>
	.pricechart {
		min-width: 0;
	}
	.line {
		fill: none;
		stroke: var(--line);
		stroke-width: 2;
		stroke-linejoin: round;
	}
	.area {
		fill: var(--line-area);
		stroke: none;
	}
	.target {
		stroke: var(--target);
		stroke-width: 1.5;
		stroke-dasharray: 5 4;
	}
	.tlabel {
		fill: var(--target);
		font-size: 10.5px;
		font-weight: 600;
	}
	circle.end,
	circle.hov {
		fill: var(--line);
		stroke: var(--surface);
		stroke-width: 2;
	}
	circle.est {
		fill: var(--surface);
		stroke: var(--estimate);
		stroke-width: 1.5;
	}
	.xt {
		fill: var(--text-3);
		font-size: 10.5px;
	}
	.empty {
		fill: var(--text-3);
		font-size: 12px;
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
	.legend .li {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		margin-right: 8px;
		white-space: nowrap;
	}
	.legend .k {
		display: inline-block;
		width: 14px;
		height: 10px;
	}
	.k.line {
		border-bottom: 2px solid var(--line);
		height: 6px;
	}
	.k.est {
		width: 9px;
		height: 9px;
		border-radius: 50%;
		border: 1.5px solid var(--estimate);
	}
</style>
