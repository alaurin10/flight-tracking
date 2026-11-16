<!-- A deal page: a deal site's page you follow (or its feed). Saving fetches it once on
     the server, which works out how to read it: its feed if it has one, else the posts
     listed on the page. New posts show on Deals; those matching what you watch alert. -->
<script lang="ts">
	import { focusFirstError, Touched } from './touched.svelte';
	import { feedErrors, toFeedIn, valid } from './validate';
	import type { DealFeed, DealFeedIn } from '../types.gen';

	interface Props {
		initial?: DealFeed;
		saving?: boolean;
		onsave: (body: DealFeedIn) => void;
		oncancel: () => void;
	}
	let { initial, saving = false, onsave, oncancel }: Props = $props();

	// svelte-ignore state_referenced_locally
	const d = $state({ url: initial?.url ?? '', name: initial?.name ?? '' });
	const t = new Touched();
	const errors = $derived(feedErrors(d));
	const show = (f: string) => t.show(f, errors);
	let form = $state<HTMLFormElement | null>(null);

	function submit(e: SubmitEvent) {
		e.preventDefault();
		t.tried = true;
		if (!valid(errors)) return focusFirstError(form);
		onsave(toFeedIn(d, initial?.status ?? 'active'));
	}
</script>

<form class="form" bind:this={form} onsubmit={submit} novalidate>
	<div class="field">
		<label for="feed-url">Page address</label>
		<input
			id="feed-url"
			type="url"
			inputmode="url"
			autocapitalize="off"
			spellcheck="false"
			bind:value={d.url}
			onblur={() => t.touch('url')}
			placeholder="escape.flights/category/flights-from-seattle/"
			aria-invalid={show('url') ? 'true' : undefined}
		/>
		{#if show('url')}<p class="err">{show('url')}</p>
		{:else}<p class="hint">
				A deal site’s page you check, or its RSS feed. It’s read every few hours.
			</p>{/if}
	</div>

	<details class="more">
		<summary>More options</summary>
		<div class="morebody">
			<div class="field">
				<label for="feed-name">Name</label>
				<input
					id="feed-name"
					type="text"
					bind:value={d.name}
					maxlength="80"
					placeholder="optional, e.g. Escape Flights"
					aria-invalid={show('name') ? 'true' : undefined}
				/>
				{#if show('name')}<p class="err">{show('name')}</p>{/if}
			</div>
		</div>
	</details>

	<div class="actions">
		<button type="button" class="btn" onclick={oncancel}>Cancel</button>
		<button type="submit" class="btn primary" disabled={saving}>
			{saving ? 'Checking the page…' : initial ? 'Save changes' : 'Start watching'}
		</button>
	</div>
</form>
