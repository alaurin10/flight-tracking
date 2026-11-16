<!-- Flexible dates: somewhere, any weekend in May (say). Flight Watch prices every
     matching date and points at the cheapest. Before saving it says how many dates that
     is and how long a full refresh takes at the request budget. -->
<script lang="ts">
	import AirportPicker from './AirportPicker.svelte';
	import { focusFirstError, Touched } from './touched.svelte';
	import {
		DOWS,
		LENGTHS,
		monthLabel,
		monthOptions,
		searchDraft,
		searchErrors,
		searchName,
		toSearchIn,
		valid,
		type Dow,
		type Length,
		type When
	} from './validate';
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
	const LENGTH_CHOICES: { id: Length; label: string; hint: string }[] = [
		...Object.entries(LENGTHS).map(([id, v]) => ({
			id: id as Length,
			label: v.label,
			hint: v.hint
		})),
		{ id: 'custom', label: 'Custom', hint: 'Pick the days and nights' }
	];
	const WHEN_CHOICES: { id: When; label: string }[] = [
		{ id: 'months', label: 'Months' },
		{ id: 'any', label: 'Any time' },
		{ id: 'dates', label: 'Dates' }
	];

	// svelte-ignore state_referenced_locally
	const d = $state(searchDraft(initial, home));
	const t = new Touched();
	const errors = $derived(searchErrors(d));
	const show = (f: string) => t.show(f, errors);
	let form = $state<HTMLFormElement | null>(null);
	let places = $state<Record<string, string>>({});
	const place = (c: string) => places[c] ?? c;
	const months = monthOptions();
	// A stored window can start before today's month; keep it in the menu.
	const fromChoices = $derived(months.includes(d.month_from) ? months : [d.month_from, ...months]);

	let origin = $state<string[]>(d.origin ? [d.origin] : []);
	$effect(() => {
		d.origin = origin[0] ?? '';
	});
	// svelte-ignore state_referenced_locally
	let more = $state(!!initial && (initial.origin !== home || initial.max_stops != null));
	$effect(() => {
		if (t.tried && errors.origin) more = true;
	});
	$effect(() => {
		if (d.month_to < d.month_from) d.month_to = d.month_from;
	});

	function toggle(dow: Dow) {
		d.dows = d.dows.includes(dow) ? d.dows.filter((x) => x !== dow) : [...d.dows, dow];
	}

	// The preview follows the form, a moment after it stops changing.
	let preview = $state<SearchPreviewOut | null>(null);
	let previewError = $state<string | null>(null);
	let seq = 0;
	$effect(() => {
		const body = toSearchIn(d, 'active', place);
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
		onsave(toSearchIn(d, initial?.status ?? 'active', place));
	}
</script>

<form class="form" bind:this={form} onsubmit={submit} novalidate>
	<AirportPicker
		id="search-to"
		label="Where to"
		multiple
		bind:value={d.destinations}
		bind:places
		error={show('destinations') || undefined}
		placeholder="City or airport; add more than one"
	/>

	<fieldset class="field">
		<legend>How long</legend>
		<div class="choices" role="group" aria-label="Trip length">
			{#each LENGTH_CHOICES as c (c.id)}
				<button
					type="button"
					class="choice"
					aria-pressed={d.length === c.id}
					onclick={() => (d.length = c.id)}
				>
					<strong>{c.label}</strong><span>{c.hint}</span>
				</button>
			{/each}
		</div>
		{#if d.length === 'custom'}
			<div class="custom">
				<div class="field">
					<span class="label" id="dows-label">Leave on</span>
					<div class="dows" role="group" aria-labelledby="dows-label">
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
				</div>
				<div class="field nights">
					<label for="search-nights">Nights</label>
					<input
						id="search-nights"
						type="text"
						inputmode="numeric"
						pattern="[0-9]*"
						bind:value={d.nights}
						onblur={() => t.touch('nights')}
						aria-invalid={show('nights') ? 'true' : undefined}
					/>
					{#if show('nights')}<p class="err">{show('nights')}</p>
					{:else}<p class="hint">0 for one way.</p>{/if}
				</div>
			</div>
		{/if}
	</fieldset>

	<fieldset class="field">
		<legend>When</legend>
		<div class="seg" role="group" aria-label="When">
			{#each WHEN_CHOICES as c (c.id)}
				<button type="button" aria-pressed={d.when === c.id} onclick={() => (d.when = c.id)}
					>{c.label}</button
				>
			{/each}
		</div>
		{#if d.when === 'months'}
			<div class="row2">
				<div class="field">
					<label for="search-month-from">From</label>
					<select id="search-month-from" bind:value={d.month_from}>
						{#each fromChoices as m (m)}<option value={m}>{monthLabel(m)}</option>{/each}
					</select>
				</div>
				<div class="field">
					<label for="search-month-to">Through</label>
					<select
						id="search-month-to"
						bind:value={d.month_to}
						aria-invalid={show('month_to') ? 'true' : undefined}
					>
						{#each fromChoices.filter((m) => m >= d.month_from) as m (m)}
							<option value={m}>{monthLabel(m)}</option>
						{/each}
					</select>
					{#if show('month_to')}<p class="err">{show('month_to')}</p>{/if}
				</div>
			</div>
		{:else if d.when === 'any'}
			<p class="hint">Every matching date from a few weeks out to several months ahead.</p>
		{:else}
			<div class="row2">
				<div class="field">
					<label for="search-start">Leave from</label>
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
					<label for="search-end">Leave by</label>
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
		<label for="search-target">Alert me when one’s under</label>
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
		{#if show('target')}<p class="err">{show('target')}</p>{/if}
	</div>

	<div class="preview" aria-live="polite" data-testid="search-preview">
		{#if preview}
			<p class="notice" class:info={!over} class:caution={over}>
				That’s <strong>{preview.pairs}</strong>
				date{preview.pairs === 1 ? '' : 's'} to price. With everything you watch ({preview.total_pairs}
				dates), each gets a fresh price about every
				<strong>{preview.refresh_days} day{preview.refresh_days === 1 ? '' : 's'}</strong>.
				{#if over}
					That’s over the limit of {preview.max_pairs} dates in all: pick fewer destinations or a shorter
					time.
				{/if}
			</p>
		{:else if previewError}
			<p class="notice bad">{previewError}</p>
		{/if}
	</div>

	<details class="more" bind:open={more}>
		<summary>More options</summary>
		<div class="morebody">
			<AirportPicker
				id="search-from"
				label="Flying from"
				bind:value={origin}
				bind:places
				error={show('origin') || undefined}
			/>
			<div class="field">
				<label for="search-stops">Stops</label>
				<select id="search-stops" bind:value={d.max_stops}>
					<option value="">Any number</option>
					<option value="0">Nonstop only</option>
					<option value="1">At most 1</option>
					<option value="2">At most 2</option>
				</select>
			</div>
			<div class="field">
				<label for="search-name">Name</label>
				<input
					id="search-name"
					type="text"
					bind:value={d.name}
					maxlength="80"
					placeholder={searchName({ ...d, name: '' }, place) || 'Made from where and when'}
					aria-invalid={show('name') ? 'true' : undefined}
				/>
				{#if show('name')}<p class="err">{show('name')}</p>{/if}
			</div>
		</div>
	</details>

	<div class="actions">
		<button type="button" class="btn" onclick={oncancel}>Cancel</button>
		<button type="submit" class="btn primary" disabled={saving}>
			{saving ? 'Saving…' : initial ? 'Save changes' : 'Start watching'}
		</button>
	</div>
</form>

<style>
	.choices {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 6px;
	}
	@media (min-width: 640px) {
		.choices {
			grid-template-columns: repeat(4, minmax(0, 1fr));
		}
	}
	.custom {
		display: grid;
		gap: 12px;
		margin-top: 10px;
	}
	.nights {
		max-width: 160px;
	}
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
	.seg {
		justify-self: start;
	}
	.seg button {
		min-height: 40px;
		padding: 5px 16px;
	}
	.preview:empty {
		display: none;
	}
</style>
