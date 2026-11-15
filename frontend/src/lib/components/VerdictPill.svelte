<!-- BOOK, LEAN BOOK, HOLD or WAIT, always with its word (colour is never the only cue). -->
<script lang="ts">
	interface Props {
		verdict: string | null | undefined;
	}
	let { verdict }: Props = $props();
	const KIND: Record<string, { cls: string; icon: string; title: string }> = {
		BOOK: { cls: 'book', icon: '✓', title: 'A good price: book it' },
		'LEAN BOOK': { cls: 'lean', icon: '↗', title: 'Better than usual: leaning towards booking' },
		HOLD: { cls: 'hold', icon: '•', title: 'An ordinary price: no reason to rush' },
		WAIT: { cls: 'wait', icon: '…', title: 'Still falling or early: wait' }
	};
	const k = $derived(verdict ? KIND[verdict] : undefined);
</script>

{#if verdict && k}
	<span class="pill verdict {k.cls}" title={k.title}
		><span aria-hidden="true">{k.icon}</span>{verdict}</span
	>
{/if}

<style>
	.verdict {
		letter-spacing: 0.02em;
	}
	.book {
		background: var(--v-book-bg);
		color: var(--v-book-ink);
	}
	.lean {
		background: var(--v-lean-bg);
		color: var(--v-lean-ink);
	}
	.hold {
		background: var(--v-hold-bg);
		color: var(--v-hold-ink);
	}
	.wait {
		background: var(--v-wait-bg);
		color: var(--v-wait-ink);
	}
</style>
