<!--
  One destination's date pairs as month calendars: each departure date shaded by price
  (darker is cheaper), the cheapest outlined, ★ under target, ↓ at its 30-day low, and
  calendar estimates dashed. Tapping a date selects it (the page shows its history).
-->
<script lang="ts">
	import type { DatePairOut } from '../types.gen';
	import { dayDate, money, monthTitle } from '../format';
	import { flagWords, months, priceBins } from '../heat';

	interface Props {
		pairs: DatePairOut[];
		currency?: string;
		selected?: number | null;
		onselect?: (p: DatePairOut) => void;
		/** The destination, for labels ("to SLC"). */
		dest: string;
	}

	let { pairs, currency = 'USD', selected = null, onselect, dest }: Props = $props();
	const bins = $derived(priceBins(pairs));
	const grid = $derived(months(pairs));
	const WEEK = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
	const WEEK_FULL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

	function cellLabel(p: DatePairOut): string {
		const when = p.return_date
			? `${dayDate(p.depart_date)} to ${dayDate(p.return_date)}`
			: `${dayDate(p.depart_date)}, one way`;
		const price = p.price === null ? 'not priced yet' : money(p.price, currency);
		const flags = flagWords(p.flags);
		return [`${when} to ${dest}: ${price}`, ...flags].join(', ');
	}
</script>

<div class="months">
	{#each grid as m (m.first)}
		<section class="month" aria-label={monthTitle(m.first)}>
			<h3>{monthTitle(m.first)}</h3>
			<div class="week" aria-hidden="true">
				{#each WEEK as d, i (i)}<span title={WEEK_FULL[i]}>{d}</span>{/each}
			</div>
			<div class="days">
				{#each Array.from({ length: m.lead }, (_, i) => i) as i (i)}<span class="blank"
					></span>{/each}
				{#each m.days as c (c.date)}
					{#if c.pair}
						{@const p = c.pair}
						{@const bin = bins.get(p.query_id)}
						<button
							class="cell {bin ? `p${bin}` : 'none'}"
							class:best={p.flags.includes('cheapest')}
							class:est={p.source === 'calendar'}
							class:on={selected === p.query_id}
							aria-pressed={selected === p.query_id}
							aria-label={cellLabel(p)}
							title={cellLabel(p)}
							data-date={p.depart_date}
							onclick={() => onselect?.(p)}
						>
							<span class="d">{c.day}</span>
							<span class="p num">{p.price === null ? '–' : money(p.price, currency)}</span>
							{#if p.flags.includes('under_target')}<span class="mk t" aria-hidden="true">★</span
								>{:else if p.flags.includes('at_low')}<span class="mk l" aria-hidden="true">↓</span
								>{/if}
						</button>
					{:else}
						<span class="plain" aria-hidden="true">{c.day}</span>
					{/if}
				{/each}
			</div>
		</section>
	{/each}
</div>

<style>
	.months {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 290px), 1fr));
		gap: 14px 18px;
	}
	.month {
		min-width: 0;
	}
	h3 {
		font-size: 0.86rem;
		font-weight: 600;
		margin: 0 0 4px 2px;
		color: var(--text-2);
	}
	.week,
	.days {
		display: grid;
		grid-template-columns: repeat(7, minmax(0, 1fr));
		gap: 3px;
	}
	.week span {
		text-align: center;
		font-size: 0.68rem;
		font-weight: 600;
		color: var(--text-3);
		padding-bottom: 2px;
	}
	.plain,
	.blank {
		min-height: 44px;
	}
	.plain {
		display: grid;
		place-items: start;
		padding: 3px 5px;
		font-size: 0.7rem;
		color: var(--text-3);
		opacity: 0.7;
	}
	.cell {
		position: relative;
		display: grid;
		grid-template-rows: auto 1fr;
		min-width: 0;
		min-height: 44px;
		padding: 3px 3px 4px;
		border: 0;
		border-radius: 10px;
		cursor: pointer;
		text-align: left;
		background: var(--p5);
		color: var(--p-ink-light);
		transition: box-shadow var(--ease);
	}
	.cell .d {
		font-size: 0.68rem;
		opacity: 0.85;
		padding-left: 2px;
	}
	.cell .p {
		align-self: end;
		justify-self: center;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: -0.02em;
		white-space: nowrap;
		max-width: 100%;
		overflow: hidden;
	}
	.cell .mk {
		position: absolute;
		top: 2px;
		right: 4px;
		font-size: 0.68rem;
		line-height: 1;
	}
	.cell.p1 {
		background: var(--p1);
		color: var(--p-ink-dark);
	}
	.cell.p2 {
		background: var(--p2);
		color: var(--p-ink-dark);
	}
	.cell.p3 {
		background: var(--p3);
		color: var(--p-ink-light);
	}
	.cell.p4 {
		background: var(--p4);
		color: var(--p-ink-light);
	}
	.cell.none {
		color: var(--text-3);
	}
	/* A calendar estimate: dashed edge, italic price. */
	.cell.est {
		outline: 1.5px dashed var(--estimate);
		outline-offset: -2px;
	}
	.cell.est .p {
		font-style: italic;
	}
	.cell.best {
		box-shadow:
			0 0 0 2px var(--surface),
			0 0 0 4px var(--text);
	}
	.cell.on {
		box-shadow:
			0 0 0 2px var(--surface),
			0 0 0 4px var(--accent);
	}
	.cell:hover {
		filter: brightness(1.05);
	}
</style>
