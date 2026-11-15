<!-- The collector's health in a word, linking to the status page. -->
<script lang="ts">
	import { resolve } from '$app/paths';
	import type { HealthSummary } from '../types.gen';

	interface Props {
		health: HealthSummary | null | undefined;
	}
	let { health }: Props = $props();
	const crit = $derived(health?.problems.some((p) => p.severity === 'crit') ?? false);
	const warns = $derived(health?.problems.length ?? 0);
</script>

{#if health}
	<a
		class="pill {health.ok && !warns ? 'good' : crit ? 'crit' : 'warn'}"
		href={resolve('/status')}
		data-testid="health-pill"
	>
		<span class="dot" aria-hidden="true"></span>
		{#if health.ok && !warns}
			Collecting normally
		{:else if crit}
			Collector needs attention
		{:else}
			{warns} warning{warns === 1 ? '' : 's'}
		{/if}
	</a>
{/if}
