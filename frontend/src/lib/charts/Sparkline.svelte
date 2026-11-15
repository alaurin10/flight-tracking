<!--
  A trip card's small chart: the recent prices as a line, the target dashed. No axes
  and no interaction: the card's numbers beside it say the same, and the trip page has
  the full chart with its table.
-->
<script lang="ts">
	import { scaleLinear } from 'd3-scale';
	import type { PricePoint } from '../types.gen';
	import { money } from '../format';
	import { linePath, priceDomain } from './geom';

	interface Props {
		points: PricePoint[];
		target?: number | null;
		currency?: string;
		width?: number;
		height?: number;
	}

	let { points, target = null, currency = 'USD', width: fixedWidth, height = 48 }: Props = $props();
	let measured = $state(0);
	const W = $derived(fixedWidth ?? measured);
	const pad = 4;
	const iw = $derived(Math.max(0, W - 2 * pad));
	const ih = $derived(height - 2 * pad);
	const x = $derived(
		scaleLinear()
			.domain([0, Math.max(1, points.length - 1)])
			.range([0, iw])
	);
	const y = $derived(
		scaleLinear()
			.domain(priceDomain([...points.map((p) => p.price), target]))
			.range([ih, 0])
	);
	const xs = $derived(points.map((_, i) => x(i)));
	const ys = $derived(points.map((p) => y(p.price)));
	const label = $derived(
		points.length >= 2
			? `Price over the last ${points.length} checks: from ${money(points[0].price, currency)} to ${money(points[points.length - 1].price, currency)}` +
					(target ? `; target ${money(target, currency)}` : '')
			: 'Not enough prices for a chart yet'
	);
</script>

<div class="spark" bind:clientWidth={measured} style:height="{height}px">
	{#if W > 0 && points.length >= 2}
		<svg width={W} {height} role="img" aria-label={label}>
			<g class="plot" transform="translate({pad},{pad})" data-iw={iw} data-ih={ih}>
				{#if target}
					<line class="mark target" x1="0" x2={iw} y1={y(target)} y2={y(target)} />
				{/if}
				<path class="mark line" d={linePath(xs, ys)} />
				<circle class="mark end" cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r="3" />
			</g>
		</svg>
	{/if}
</div>

<style>
	.spark {
		min-width: 0;
	}
	svg {
		display: block;
	}
	.line {
		fill: none;
		stroke: var(--line);
		stroke-width: 1.8;
		stroke-linejoin: round;
	}
	.target {
		stroke: var(--target);
		stroke-width: 1.2;
		stroke-dasharray: 4 3;
	}
	.end {
		fill: var(--line);
	}
</style>
