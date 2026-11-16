<!-- The "⋯" menu on a Manage row: a button that opens a short list of actions. Arrow keys
     move through it, Escape or a click elsewhere closes it, and focus returns to the
     button. -->
<script lang="ts">
	import { tick } from 'svelte';

	interface Item {
		label: string;
		run: () => void;
		danger?: boolean;
	}
	let { label, items }: { label: string; items: Item[] } = $props();

	let open = $state(false);
	let button = $state<HTMLButtonElement | null>(null);
	let menu = $state<HTMLUListElement | null>(null);

	const entries = () => [...(menu?.querySelectorAll<HTMLButtonElement>('button') ?? [])];

	async function show() {
		open = true;
		await tick();
		entries()[0]?.focus();
	}
	function close(refocus = true) {
		open = false;
		if (refocus) button?.focus();
	}
	function onkey(e: KeyboardEvent) {
		const list = entries();
		const i = list.indexOf(document.activeElement as HTMLButtonElement);
		if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			close();
		} else if (e.key === 'ArrowDown') {
			e.preventDefault();
			list[(i + 1) % list.length]?.focus();
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			list[(i - 1 + list.length) % list.length]?.focus();
		} else if (e.key === 'Tab') {
			close(false);
		}
	}
	$effect(() => {
		if (!open) return;
		const away = (e: PointerEvent) => {
			if (!menu?.contains(e.target as Node) && !button?.contains(e.target as Node)) close(false);
		};
		document.addEventListener('pointerdown', away);
		return () => document.removeEventListener('pointerdown', away);
	});
</script>

<div class="wrap">
	<button
		bind:this={button}
		type="button"
		class="dots"
		aria-label={label}
		aria-haspopup="menu"
		aria-expanded={open}
		onclick={() => (open ? close() : show())}
	>
		<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"
			><circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle
				cx="19"
				cy="12"
				r="1.8"
			/></svg
		>
	</button>
	{#if open}
		<ul class="menu" role="menu" aria-label={label} bind:this={menu} onkeydown={onkey}>
			{#each items as it (it.label)}
				<li role="none">
					<button
						type="button"
						role="menuitem"
						class:danger={it.danger}
						onclick={() => {
							close();
							it.run();
						}}>{it.label}</button
					>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.wrap {
		position: relative;
	}
	.dots {
		width: 44px;
		height: 44px;
		display: grid;
		place-items: center;
		border: 0;
		border-radius: 999px;
		background: transparent;
		color: var(--text-2);
		cursor: pointer;
	}
	.dots:hover,
	.dots[aria-expanded='true'] {
		background: var(--surface-2);
		color: var(--text);
	}
	.dots svg {
		fill: currentColor;
	}
	.menu {
		position: absolute;
		right: 0;
		top: calc(100% + 4px);
		z-index: 30;
		min-width: 180px;
		margin: 0;
		padding: 4px;
		list-style: none;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 14px;
		box-shadow: var(--glass-shadow);
	}
	.menu button {
		width: 100%;
		min-height: 44px;
		padding: 0 12px;
		border: 0;
		border-radius: 10px;
		background: transparent;
		text-align: left;
		font-size: 0.95rem;
		cursor: pointer;
	}
	.menu button:hover,
	.menu button:focus-visible {
		background: var(--surface-2);
	}
	.menu button.danger {
		color: var(--warn-ink);
	}
</style>
