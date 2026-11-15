<!-- Add or change a deal watch: which deal-feed headlines alert. A headline matches when it
     has one of the origin words, one of the destination words (or a place in the region),
     and a price at or under the maximum. "Try a headline" asks the server which active
     watches a headline would match. -->
<script lang="ts">
	import { focusFirstError, Touched } from './touched.svelte';
	import { dealWatchDraft, dealWatchErrors, toDealWatchIn, valid } from './validate';
	import { testHeadline } from '../api';
	import { friendlyError } from '../errors';
	import { money } from '../format';
	import type { DealTestOut, DealWatch, DealWatchIn } from '../types.gen';

	interface Props {
		initial?: DealWatch;
		regions?: string[];
		saving?: boolean;
		onsave: (body: DealWatchIn) => void;
		oncancel: () => void;
	}
	let { initial, regions = [], saving = false, onsave, oncancel }: Props = $props();

	// svelte-ignore state_referenced_locally
	const d = $state(dealWatchDraft(initial, regions));
	const t = new Touched();
	const errors = $derived(dealWatchErrors(d));
	const show = (f: string) => t.show(f, errors);
	let form = $state<HTMLFormElement | null>(null);

	const title = (r: string) => r.replace(/\b\w/g, (c) => c.toUpperCase());

	function submit(e: SubmitEvent) {
		e.preventDefault();
		t.tried = true;
		if (!valid(errors)) return focusFirstError(form);
		onsave(toDealWatchIn(d, initial?.status ?? 'active'));
	}

	let headline = $state('');
	let tested = $state<DealTestOut | null>(null);
	let testError = $state<string | null>(null);
	let testing = $state(false);
	async function tryHeadline() {
		if (!headline.trim() || testing) return;
		testing = true;
		testError = null;
		try {
			tested = await testHeadline(headline.trim());
		} catch (e) {
			tested = null;
			testError = friendlyError(e);
		} finally {
			testing = false;
		}
	}
</script>

<form class="form" bind:this={form} onsubmit={submit} novalidate>
	<div class="field">
		<label for="watch-name">Name</label>
		<input
			id="watch-name"
			type="text"
			bind:value={d.name}
			onblur={() => t.touch('name')}
			placeholder="e.g. Hawaii from Seattle"
			maxlength="80"
			aria-invalid={show('name') ? 'true' : undefined}
		/>
		{#if show('name')}<p class="err">{show('name')}</p>{/if}
	</div>

	<div class="field">
		<label for="watch-origins">From (any of these words)</label>
		<input
			id="watch-origins"
			type="text"
			bind:value={d.origins}
			onblur={() => t.touch('origins')}
			placeholder="e.g. Seattle, SEA"
			aria-invalid={show('origins') ? 'true' : undefined}
		/>
		{#if show('origins')}<p class="err">{show('origins')}</p>
		{:else}<p class="hint">Separate words with commas. Empty matches any origin.</p>{/if}
	</div>

	<div class="row2">
		<div class="field">
			<label for="watch-region">To a region</label>
			<select id="watch-region" bind:value={d.region}>
				<option value="">No region</option>
				{#each regions as r (r)}<option value={r}>{title(r)}</option>{/each}
			</select>
		</div>
		<div class="field">
			<label for="watch-dests">Or these words</label>
			<input
				id="watch-dests"
				type="text"
				bind:value={d.destinations}
				onblur={() => t.touch('destinations')}
				placeholder="e.g. Honolulu, Maui"
				aria-invalid={show('destinations') ? 'true' : undefined}
			/>
			{#if show('destinations')}<p class="err">{show('destinations')}</p>{/if}
		</div>
	</div>
	<p class="hint">
		A region stands for its countries and cities (Hawaii: Honolulu, Maui, Kona…). With neither, any
		destination matches.
	</p>

	<div class="field">
		<label for="watch-max">Maximum price</label>
		<div class="money">
			<span aria-hidden="true">$</span>
			<input
				id="watch-max"
				type="text"
				inputmode="decimal"
				bind:value={d.max}
				onblur={() => t.touch('max')}
				placeholder="optional"
				aria-invalid={show('max') ? 'true' : undefined}
			/>
		</div>
		{#if show('max')}<p class="err">{show('max')}</p>{/if}
	</div>

	<div class="actions">
		<button type="button" class="btn" onclick={oncancel}>Cancel</button>
		<button type="submit" class="btn primary" disabled={saving}>
			{saving ? 'Saving…' : initial ? 'Save changes' : 'Add deal watch'}
		</button>
	</div>
</form>

<section class="try" aria-labelledby="try-h">
	<h3 id="try-h">Try a headline</h3>
	<p class="hint">Paste a deal’s headline to see which saved, active watches it would match.</p>
	<div class="tryrow">
		<label class="sr-only" for="try-headline">Headline</label>
		<input
			id="try-headline"
			class="input"
			type="text"
			bind:value={headline}
			placeholder="Alaska: Seattle – Honolulu. $298 roundtrip"
			onkeydown={(e) => {
				if (e.key === 'Enter') {
					e.preventDefault();
					tryHeadline();
				}
			}}
		/>
		<button type="button" class="btn" disabled={!headline.trim() || testing} onclick={tryHeadline}
			>{testing ? 'Trying…' : 'Try it'}</button
		>
	</div>
	<div aria-live="polite">
		{#if tested}
			<p class="notice info" data-testid="headline-result">
				Price read: <strong>{tested.price === null ? 'none found' : money(tested.price)}</strong>.
				{#if tested.matched}
					It would alert for <strong>{tested.matched}</strong>{tested.matches.length > 1
						? ` (it also matches ${tested.matches.filter((m) => m !== tested?.matched).join(', ')})`
						: ''}.
				{:else if tested.matches.length}
					It matches {tested.matches.join(', ')}, but not under a maximum price.
				{:else}
					No active watch matches it.
				{/if}
			</p>
		{:else if testError}
			<p class="notice bad">{testError}</p>
		{/if}
	</div>
</section>

<style>
	.try {
		margin-top: 18px;
		padding-top: 14px;
		border-top: 1px solid var(--grid);
		display: grid;
		gap: 8px;
	}
	h3 {
		margin: 0;
		font-size: 0.95rem;
	}
	.hint {
		font-size: 0.8rem;
		color: var(--text-3);
		margin: 0;
	}
	.tryrow {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		gap: 8px;
	}
	.tryrow .btn {
		min-height: 44px;
	}
</style>
