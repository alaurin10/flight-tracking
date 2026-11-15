<!-- The alert thresholds the owner can change (stored on the server, in `settings`). -->
<script lang="ts">
	import { focusFirstError, Touched } from './touched.svelte';
	import { settingsDraft, settingsErrors, toSettings, valid } from './validate';
	import type { AlertSettings } from '../types.gen';

	interface Props {
		initial: AlertSettings;
		saving?: boolean;
		onsave: (body: AlertSettings) => void;
	}
	let { initial, saving = false, onsave }: Props = $props();

	// svelte-ignore state_referenced_locally
	const d = $state(settingsDraft(initial));
	const t = new Touched();
	const errors = $derived(settingsErrors(d));
	const show = (f: string) => t.show(f, errors);
	let form = $state<HTMLFormElement | null>(null);

	function submit(e: SubmitEvent) {
		e.preventDefault();
		t.tried = true;
		if (!valid(errors)) return focusFirstError(form);
		onsave(toSettings(d));
	}
</script>

<form class="form" bind:this={form} onsubmit={submit} novalidate>
	<div class="row2">
		<div class="field">
			<label for="set-days">Quiet days after an alert</label>
			<input
				id="set-days"
				type="text"
				inputmode="numeric"
				bind:value={d.dedup_days}
				onblur={() => t.touch('dedup_days')}
				aria-invalid={show('dedup_days') ? 'true' : undefined}
			/>
			{#if show('dedup_days')}<p class="err">{show('dedup_days')}</p>
			{:else}<p class="hint">
					A date pair that alerted doesn’t alert again for this many days…
				</p>{/if}
		</div>
		<div class="field">
			<label for="set-drop">…unless it falls a further (%)</label>
			<input
				id="set-drop"
				type="text"
				inputmode="decimal"
				bind:value={d.dedup_further_drop_pct}
				onblur={() => t.touch('dedup_further_drop_pct')}
				aria-invalid={show('dedup_further_drop_pct') ? 'true' : undefined}
			/>
			{#if show('dedup_further_drop_pct')}<p class="err">{show('dedup_further_drop_pct')}</p>
			{:else}<p class="hint">Percent below the price that last alerted.</p>{/if}
		</div>
	</div>
	<label class="check">
		<input type="checkbox" bind:checked={d.percentile_alerts} />
		Also alert on prices in a route’s cheapest 20% (needs a month of history)
	</label>
	<div class="actions">
		<button type="submit" class="btn primary" disabled={saving}>
			{saving ? 'Saving…' : 'Save alert settings'}
		</button>
	</div>
</form>
