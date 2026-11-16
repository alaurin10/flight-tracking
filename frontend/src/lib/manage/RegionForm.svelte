<!-- A region: anywhere in Europe in the summer (say). Flight Watch reads your deal pages
     and feeds and alerts on posts from home to the region, in those months, under the
     price. "Try a headline" (under More options) shows what a post would match. -->
<script lang="ts">
	import { focusFirstError, Touched } from './touched.svelte';
	import {
		MONTHS,
		SEASONS,
		monthsPhrase,
		regionDraft,
		regionErrors,
		regionName,
		toDealWatchIn,
		valid
	} from './validate';
	import { testHeadline } from '../api';
	import { friendlyError } from '../errors';
	import { money } from '../format';
	import type { DealTestOut, DealWatch, DealWatchIn } from '../types.gen';

	interface Props {
		initial?: DealWatch;
		regions?: string[];
		/** Words for home, e.g. "Seattle, SEA": what a post must say it leaves from. */
		homeWords?: string;
		saving?: boolean;
		onsave: (body: DealWatchIn) => void;
		oncancel: () => void;
	}
	let { initial, regions = [], homeWords = '', saving = false, onsave, oncancel }: Props = $props();

	// svelte-ignore state_referenced_locally
	const d = $state(regionDraft(initial, regions, homeWords));
	const t = new Touched();
	const errors = $derived(regionErrors(d));
	const show = (f: string) => t.show(f, errors);
	let form = $state<HTMLFormElement | null>(null);
	let more = $state(false);
	$effect(() => {
		if (t.tried && errors.origins) more = true;
	});
	// Home's words arrive after the form opens (they need the airport's city).
	$effect(() => {
		if (!initial && homeWords && !t.tried && !d.origins) d.origins = homeWords;
	});

	const title = (r: string) => r.replace(/\b\w/g, (c) => c.toUpperCase());
	const toggleMonth = (m: number) =>
		(d.months = d.months.includes(m) ? d.months.filter((x) => x !== m) : [...d.months, m]);
	const isSeason = (ms: number[]) =>
		d.months.length === ms.length && ms.every((m) => d.months.includes(m));

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
	<fieldset class="field">
		<legend>Where</legend>
		<div class="choices" role="group" aria-label="Region">
			{#each regions as r (r)}
				<button
					type="button"
					class="choice"
					aria-pressed={d.region === r}
					class:invalid={!!show('region')}
					onclick={() => (d.region = d.region === r ? '' : r)}><strong>{title(r)}</strong></button
				>
			{/each}
		</div>
		<div class="field">
			<label for="region-places">Or name places</label>
			<input
				id="region-places"
				type="text"
				bind:value={d.places}
				onblur={() => t.touch('places')}
				placeholder="e.g. Lisbon, Porto, Madeira"
				aria-invalid={show('places') ? 'true' : undefined}
			/>
			{#if show('region')}<p class="err">{show('region')}</p>
			{:else if show('places')}<p class="err">{show('places')}</p>
			{:else}<p class="hint">Separate with commas. A post naming any of them matches.</p>{/if}
		</div>
	</fieldset>

	<fieldset class="field">
		<legend>When</legend>
		<div class="seasons" role="group" aria-label="Seasons">
			<button type="button" aria-pressed={d.months.length === 0} onclick={() => (d.months = [])}
				>Any time</button
			>
			{#each SEASONS as s (s.label)}
				<button
					type="button"
					aria-pressed={isSeason(s.months)}
					onclick={() => (d.months = [...s.months])}>{s.label}</button
				>
			{/each}
		</div>
		<div class="months" role="group" aria-label="Months">
			{#each MONTHS as m, i (m)}
				<button
					type="button"
					aria-pressed={d.months.includes(i + 1)}
					onclick={() => toggleMonth(i + 1)}>{m}</button
				>
			{/each}
		</div>
		<p class="hint">
			{d.months.length
				? `Posts for ${monthsPhrase(d.months)}, and posts that don’t say when.`
				: 'Posts for any time of year.'}
		</p>
	</fieldset>

	<div class="field">
		<label for="region-max">Alert me when it’s under</label>
		<div class="money">
			<span aria-hidden="true">$</span>
			<input
				id="region-max"
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

	<p class="hint">
		Fares come from your deal pages and feeds: a post from home to {d.region
			? title(d.region)
			: 'these places'} alerts you.
	</p>

	<details class="more" bind:open={more}>
		<summary>More options</summary>
		<div class="morebody">
			<div class="field">
				<label for="region-origins">Leaving from (any of these words)</label>
				<input
					id="region-origins"
					type="text"
					bind:value={d.origins}
					onblur={() => t.touch('origins')}
					placeholder="e.g. Seattle, SEA, West Coast"
					aria-invalid={show('origins') ? 'true' : undefined}
				/>
				{#if show('origins')}<p class="err">{show('origins')}</p>
				{:else}<p class="hint">Empty matches deals from anywhere.</p>{/if}
			</div>
			<div class="field">
				<label for="region-name">Name</label>
				<input
					id="region-name"
					type="text"
					bind:value={d.name}
					maxlength="80"
					placeholder={regionName({ ...d, name: '' }) || 'Made from where and when'}
					aria-invalid={show('name') ? 'true' : undefined}
				/>
				{#if show('name')}<p class="err">{show('name')}</p>{/if}
			</div>
			<div class="try field">
				<label for="try-headline">Try a headline</label>
				<p class="hint">Paste a deal’s headline to see what it would alert for (saved watches).</p>
				<div class="tryrow">
					<input
						class="input"
						type="text"
						id="try-headline"
						bind:value={headline}
						placeholder="Seattle to Paris $412 round-trip [Jun-Aug]"
						onkeydown={(e) => {
							if (e.key === 'Enter') {
								e.preventDefault();
								tryHeadline();
							}
						}}
					/>
					<button
						type="button"
						class="btn"
						disabled={!headline.trim() || testing}
						onclick={tryHeadline}>{testing ? 'Trying…' : 'Try it'}</button
					>
				</div>
				<div aria-live="polite">
					{#if tested}
						<p class="notice info" data-testid="headline-result">
							Price <strong>{tested.price === null ? 'not found' : money(tested.price)}</strong
							>{tested.months.length ? `, for ${monthsPhrase(tested.months)}` : ''}.
							{#if tested.matched}
								It would alert for <strong>{tested.matched}</strong>{tested.matches.length > 1
									? ` (it also matches ${tested.matches.filter((m) => m !== tested?.matched).join(', ')})`
									: ''}.
							{:else}
								Nothing you watch matches it.
							{/if}
						</p>
					{:else if testError}
						<p class="notice bad">{testError}</p>
					{/if}
				</div>
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
		grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
		gap: 6px;
	}
	.seasons {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.seasons button,
	.months button {
		min-height: 40px;
		border-radius: 12px;
		border: 1px solid var(--border);
		background: var(--surface);
		font-size: 0.88rem;
		font-weight: 500;
		cursor: pointer;
		padding: 0 12px;
	}
	.months {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: 4px;
		margin-top: 6px;
	}
	.months button {
		padding: 0;
	}
	.seasons button[aria-pressed='true'],
	.months button[aria-pressed='true'] {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--on-accent);
	}
	.try {
		display: grid;
		gap: 6px;
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
