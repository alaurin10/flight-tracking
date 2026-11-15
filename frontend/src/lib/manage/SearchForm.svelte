<!-- Add or change a flexible search: some weekdays, a number of nights, one or more
     destinations, in a date window or rolling. Before saving it says how many date pairs
     that is, and how long one detail fetch of everything tracked takes at the budget. -->
<script lang="ts">
	import AirportPicker from './AirportPicker.svelte';
	import { focusFirstError, Touched } from './touched.svelte';
	import { DOWS, searchDraft, searchErrors, toSearchIn, valid, type Dow } from './validate';
	import { previewSearch } from '../api';
	import { friendlyError } from '../errors';
	import type { Search, SearchIn, SearchPreviewOut } from '../types.gen';

	interface Props {
		initial?: Search;
		home?: string;
		saving?: boolean;
		onsave: (body: SearchIn) => void;
		oncancel: () => void;
	}
	let { initial, home = '', saving = false, onsave, oncancel }: Props = $props();

	const DOW_LABEL: Record<Dow, string> = {
		MON: 'Mon',
		TUE: 'Tue',
		WED: 'Wed',
		THU: 'Thu',
		FRI: 'Fri',
		SAT: 'Sat',
		SUN: 'Sun'
	};

	// svelte-ignore state_referenced_locally
	const d = $state(searchDraft(initial, home));
	const t = new Touched();
	const errors = $derived(searchErrors(d));
	const show = (f: string) => t.show(f, errors);
	let form = $state<HTMLFormElement | null>(null);

	let origin = $state<string[]>(d.origin ? [d.origin] : []);
	$effect(() => {
		d.origin = origin[0] ?? '';
	});

	function toggle(dow: Dow) {
		d.dows = d.dows.includes(dow) ? d.dows.filter((x) => x !== dow) : [...d.dows, dow];
		t.touch('dows');
	}

	// The preview follows the form, a moment after it stops changing.
	let preview = $state<SearchPreviewOut | null>(null);
	let previewError = $state<string | null>(null);
	let seq = 0;
	$effect(() => {
		const body = toSearchIn(d);
		const ok = valid(errors);
		const n = ++seq;
		if (!ok) {
			preview = null;
			previewError = null;
			return;
		}
		const timer = setTimeout(() => {
			previewSearch(body, initial?.id).then(
				(p) => {
					if (n === seq) [preview, previewError] = [p, null];
				},
				(e: unknown) => {
					if (n === seq) [preview, previewError] = [null, friendlyError(e)];
				}
			);
		}, 400);
		return () => clearTimeout(timer);
	});
	const over = $derived(preview !== null && preview.total_pairs > preview.max_pairs);

	function submit(e: SubmitEvent) {
		e.preventDefault();
		t.tried = true;
		if (!valid(errors)) return focusFirstError(form);
		onsave(toSearchIn(d, initial?.status ?? 'active'));
	}
</script>

<form class="form" bind:this={form} onsubmit={submit} novalidate>
	<div class="field">
		<label for="search-name">Name</label>
		<input
			id="search-name"
			type="text"
			bind:value={d.name}
			onblur={() => t.touch('name')}
			placeholder="e.g. Ski weekends"
			maxlength="80"
			aria-invalid={show('name') ? 'true' : undefined}
		/>
		{#if show('name')}<p class="err">{show('name')}</p>{/if}
	</div>

	<AirportPicker
		id="search-from"
		label="From"
		bind:value={origin}
		error={show('origin') || undefined}
	/>
	<AirportPicker
		id="search-to"
		label="To (one or more)"
		multiple
		bind:value={d.destinations}
		error={show('destinations') || undefined}
		hint="Each destination is priced on every date."
	/>

	<fieldset class="field">
		<legend>Leave on</legend>
		<div class="dows" role="group" aria-label="Departure weekdays">
			{#each DOWS as dow (dow)}
				<button
					type="button"
					aria-pressed={d.dows.includes(dow)}
					class:invalid={!!show('dows')}
					onclick={() => toggle(dow)}>{DOW_LABEL[dow]}</button
				>
			{/each}
		</div>
		{#if show('dows')}<p class="err">{show('dows')}</p>{/if}
	</fieldset>

	<div class="row2">
		<div class="field">
			<label for="search-nights">Nights</label>
			<input
				id="search-nights"
				type="text"
				inputmode="numeric"
				pattern="[0-9]*"
				bind:value={d.nights}
				onblur={() => t.touch('nights')}
				aria-invalid={show('nights') ? 'true' : undefined}
				aria-describedby="search-nights-hint"
			/>
			{#if show('nights')}<p class="err">{show('nights')}</p>
			{:else}<p class="hint" id="search-nights-hint">0 for one way.</p>{/if}
		</div>
		<div class="field">
			<label for="search-stops">Stops</label>
			<select id="search-stops" bind:value={d.max_stops}>
				<option value="">Any number</option>
				<option value="0">Nonstop only</option>
				<option value="1">At most 1</option>
				<option value="2">At most 2</option>
			</select>
		</div>
	</div>

	<fieldset class="field">
		<legend>Dates</legend>
		<div class="seg" role="group" aria-label="Date window">
			<button type="button" aria-pressed={d.rolling} onclick={() => (d.rolling = true)}
				>Rolling</button
			>
			<button type="button" aria-pressed={!d.rolling} onclick={() => (d.rolling = false)}
				>Between two dates</button
			>
		</div>
		{#if d.rolling}
			<p class="hint">Every matching date from a few weeks out to several months ahead.</p>
		{:else}
			<div class="row2">
				<div class="field">
					<label for="search-start">First departure</label>
					<input
						id="search-start"
						type="date"
						bind:value={d.window_start}
						onblur={() => t.touch('window_start')}
						aria-invalid={show('window_start') ? 'true' : undefined}
					/>
					{#if show('window_start')}<p class="err">{show('window_start')}</p>{/if}
				</div>
				<div class="field">
					<label for="search-end">Last departure</label>
					<input
						id="search-end"
						type="date"
						bind:value={d.window_end}
						min={d.window_start || undefined}
						onblur={() => t.touch('window_end')}
						aria-invalid={show('window_end') ? 'true' : undefined}
					/>
					{#if show('window_end')}<p class="err">{show('window_end')}</p>{/if}
				</div>
			</div>
		{/if}
	</fieldset>

	<div class="field">
		<label for="search-target">Target price</label>
		<div class="money">
			<span aria-hidden="true">$</span>
			<input
				id="search-target"
				type="text"
				inputmode="decimal"
				bind:value={d.target}
				onblur={() => t.touch('target')}
				placeholder="optional"
				aria-invalid={show('target') ? 'true' : undefined}
			/>
		</div>
		{#if show('target')}<p class="err">{show('target')}</p>
		{:else}<p class="hint">In dollars. Dates at or under it are marked and can alert.</p>{/if}
	</div>

	<div class="preview" aria-live="polite" data-testid="search-preview">
		{#if preview}
			<p class="notice" class:info={!over} class:caution={over}>
				This tracks <strong>{preview.pairs}</strong>
				date pair{preview.pairs === 1 ? '' : 's'}
				({preview.total_pairs} of {preview.max_pairs} in all). At {preview.detail_per_day} detail fetches
				a day, refreshing every tracked pair takes about
				<strong>{preview.refresh_days} day{preview.refresh_days === 1 ? '' : 's'}</strong>; the
				calendar sweep prices each date every day in the meantime.
				{#if over}
					That’s over the limit, so it can’t be saved: fewer weekdays, destinations or a shorter
					window.
				{/if}
			</p>
		{:else if previewError}
			<p class="notice bad">{previewError}</p>
		{/if}
	</div>

	<div class="actions">
		<button type="button" class="btn" onclick={oncancel}>Cancel</button>
		<button type="submit" class="btn primary" disabled={saving}>
			{saving ? 'Saving…' : initial ? 'Save changes' : 'Add search'}
		</button>
	</div>
</form>

<style>
	.dows {
		display: grid;
		grid-template-columns: repeat(7, minmax(0, 1fr));
		gap: 4px;
	}
	.dows button {
		min-height: 44px;
		border-radius: 12px;
		border: 1px solid var(--border);
		background: var(--surface);
		font-size: 0.88rem;
		font-weight: 500;
		cursor: pointer;
		padding: 0;
	}
	.dows button[aria-pressed='true'] {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--on-accent);
	}
	.dows button.invalid {
		border-color: var(--warn);
	}
	.seg button {
		min-height: 40px;
	}
	.preview:empty {
		display: none;
	}
</style>
