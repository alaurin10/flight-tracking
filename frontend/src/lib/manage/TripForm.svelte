<!-- Add or change a trip: one fixed date pair, priced until it departs. -->
<script lang="ts">
	import AirportPicker from './AirportPicker.svelte';
	import { focusFirstError, Touched } from './touched.svelte';
	import { toTripIn, tripDraft, tripErrors, valid } from './validate';
	import type { Trip, TripIn } from '../types.gen';

	interface Props {
		initial?: Trip;
		home?: string;
		saving?: boolean;
		onsave: (body: TripIn) => void;
		oncancel: () => void;
	}
	let { initial, home = '', saving = false, onsave, oncancel }: Props = $props();

	// The page mounts a fresh form for each trip, so copying `initial` once is intended.
	// svelte-ignore state_referenced_locally
	const d = $state(tripDraft(initial, home));
	const t = new Touched();
	const errors = $derived(tripErrors(d));
	const show = (f: string) => t.show(f, errors);
	let form = $state<HTMLFormElement | null>(null);

	let origin = $state<string[]>(d.origin ? [d.origin] : []);
	let destination = $state<string[]>(d.destination ? [d.destination] : []);
	$effect(() => {
		d.origin = origin[0] ?? '';
		d.destination = destination[0] ?? '';
	});

	function submit(e: SubmitEvent) {
		e.preventDefault();
		t.tried = true;
		if (!valid(errors)) return focusFirstError(form);
		onsave(toTripIn(d, initial?.status ?? 'active'));
	}
</script>

<form class="form" bind:this={form} onsubmit={submit} novalidate>
	<div class="field">
		<label for="trip-name">Name</label>
		<input
			id="trip-name"
			type="text"
			bind:value={d.name}
			onblur={() => t.touch('name')}
			placeholder="e.g. Tokyo in spring"
			maxlength="80"
			aria-invalid={show('name') ? 'true' : undefined}
		/>
		{#if show('name')}<p class="err">{show('name')}</p>{/if}
	</div>

	<div class="row2">
		<AirportPicker
			id="trip-from"
			label="From"
			bind:value={origin}
			error={show('origin') || undefined}
		/>
		<AirportPicker
			id="trip-to"
			label="To"
			bind:value={destination}
			error={show('destination') || undefined}
		/>
	</div>

	<label class="check">
		<input type="checkbox" bind:checked={d.oneWay} /> One way
	</label>

	<div class="row2">
		<div class="field">
			<label for="trip-depart">Depart</label>
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
				<label for="trip-return">Return</label>
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

	<div class="row2">
		<div class="field">
			<label for="trip-target">Target price</label>
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
					aria-describedby="trip-target-hint"
				/>
			</div>
			{#if show('target')}<p class="err">{show('target')}</p>
			{:else}<p class="hint" id="trip-target-hint">
					In dollars. At or under it, the verdict is BOOK.
				</p>{/if}
		</div>
		<div class="field">
			<label for="trip-stops">Stops</label>
			<select id="trip-stops" bind:value={d.max_stops}>
				<option value="">Any number</option>
				<option value="0">Nonstop only</option>
				<option value="1">At most 1</option>
				<option value="2">At most 2</option>
			</select>
		</div>
	</div>

	<div class="field">
		<label for="trip-notes">Notes</label>
		<textarea
			id="trip-notes"
			bind:value={d.notes}
			maxlength="2000"
			placeholder="optional; only you see them here"></textarea>
		{#if show('notes')}<p class="err">{show('notes')}</p>{/if}
	</div>

	<div class="actions">
		<button type="button" class="btn" onclick={oncancel}>Cancel</button>
		<button type="submit" class="btn primary" disabled={saving}>
			{saving ? 'Saving…' : initial ? 'Save changes' : 'Add trip'}
		</button>
	</div>
</form>
