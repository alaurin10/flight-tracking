<!-- A trip on set dates: where to and when; Flight Watch finds the cheapest flight and
     prices it until it departs. Everything else is under "More options". -->
<script lang="ts">
	import AirportPicker from './AirportPicker.svelte';
	import { focusFirstError, Touched } from './touched.svelte';
	import { toTripIn, tripDraft, tripErrors, tripName, valid } from './validate';
	import type { Trip, TripIn } from '../types.gen';

	interface Props {
		initial?: Trip;
		/** A new trip's fields, e.g. from a found deal's "Track this". */
		prefill?: Partial<TripIn>;
		home?: string;
		saving?: boolean;
		onsave: (body: TripIn) => void;
		oncancel: () => void;
	}
	let { initial, prefill, home = '', saving = false, onsave, oncancel }: Props = $props();

	// The page mounts a fresh form for each trip, so copying `initial` once is intended.
	// svelte-ignore state_referenced_locally
	const d = $state(tripDraft(initial ?? prefill, home));
	const t = new Touched();
	const errors = $derived(tripErrors(d));
	const show = (f: string) => t.show(f, errors);
	let form = $state<HTMLFormElement | null>(null);
	let places = $state<Record<string, string>>({});
	const place = (c: string) => places[c] ?? c;

	let origin = $state<string[]>(d.origin ? [d.origin] : []);
	let destination = $state<string[]>(d.destination ? [d.destination] : []);
	$effect(() => {
		d.origin = origin[0] ?? '';
		d.destination = destination[0] ?? '';
	});
	// "More options" opens by itself when something in it needs attention or is set.
	// svelte-ignore state_referenced_locally
	let more = $state(
		!!initial && (initial.origin !== home || initial.max_stops != null || !!initial.notes)
	);
	$effect(() => {
		if (t.tried && errors.origin) more = true;
	});

	function submit(e: SubmitEvent) {
		e.preventDefault();
		t.tried = true;
		if (!valid(errors)) return focusFirstError(form);
		onsave(toTripIn(d, initial?.status ?? 'active', place));
	}
</script>

<form class="form" bind:this={form} onsubmit={submit} novalidate>
	<AirportPicker
		id="trip-to"
		label="Where to"
		bind:value={destination}
		bind:places
		error={show('destination') || undefined}
		placeholder="City or airport, e.g. Tokyo"
	/>

	<fieldset class="field">
		<legend>When</legend>
		<div class="row2">
			<div class="field">
				<label for="trip-depart">Leave</label>
				<input
					id="trip-depart"
					type="date"
					bind:value={d.depart_date}
					onblur={() => t.touch('depart_date')}
					aria-invalid={show('depart_date') ? 'true' : undefined}
				/>
				{#if show('depart_date')}<p class="err">{show('depart_date')}</p>{/if}
			</div>
			{#if !d.oneWay}
				<div class="field">
					<label for="trip-return">Come back</label>
					<input
						id="trip-return"
						type="date"
						bind:value={d.return_date}
						min={d.depart_date || undefined}
						onblur={() => t.touch('return_date')}
						aria-invalid={show('return_date') ? 'true' : undefined}
					/>
					{#if show('return_date')}<p class="err">{show('return_date')}</p>{/if}
				</div>
			{/if}
		</div>
		<label class="check"><input type="checkbox" bind:checked={d.oneWay} /> One way</label>
	</fieldset>

	<div class="field">
		<label for="trip-target">Alert me when it’s under</label>
		<div class="money">
			<span aria-hidden="true">$</span>
			<input
				id="trip-target"
				type="text"
				inputmode="decimal"
				bind:value={d.target}
				onblur={() => t.touch('target')}
				placeholder="optional"
				aria-invalid={show('target') ? 'true' : undefined}
			/>
		</div>
		{#if show('target')}<p class="err">{show('target')}</p>
		{:else}<p class="hint">
				Without one, you’re alerted at the lowest price seen and when it drops unusually.
			</p>{/if}
	</div>

	<details class="more" bind:open={more}>
		<summary>More options</summary>
		<div class="morebody">
			<AirportPicker
				id="trip-from"
				label="Flying from"
				bind:value={origin}
				bind:places
				error={show('origin') || undefined}
			/>
			<div class="field">
				<label for="trip-stops">Stops</label>
				<select id="trip-stops" bind:value={d.max_stops}>
					<option value="">Any number</option>
					<option value="0">Nonstop only</option>
					<option value="1">At most 1</option>
					<option value="2">At most 2</option>
				</select>
			</div>
			<div class="field">
				<label for="trip-name">Name</label>
				<input
					id="trip-name"
					type="text"
					bind:value={d.name}
					maxlength="80"
					placeholder={tripName({ ...d, name: '' }, place) || 'Made from where and when'}
					aria-invalid={show('name') ? 'true' : undefined}
				/>
				{#if show('name')}<p class="err">{show('name')}</p>{/if}
			</div>
			<div class="field">
				<label for="trip-notes">Notes</label>
				<textarea id="trip-notes" bind:value={d.notes} maxlength="2000" placeholder="optional"
				></textarea>
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
