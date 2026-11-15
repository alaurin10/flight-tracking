<!-- What went wrong loading a page, in words, with a way on: retry, or back home. -->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { ApiError } from '../api';

	interface Props {
		error: unknown;
		/** The heading when the thing asked for doesn't exist (a 404). */
		notFound?: string;
		retry?: () => void;
	}
	let { error, notFound = 'There’s nothing here', retry }: Props = $props();
	const status = $derived(error instanceof ApiError ? error.status : -1);
	const unreachable = $derived(error instanceof ApiError && error.unreachable);
</script>

<div class="card note" role="alert">
	{#if unreachable}
		<h2>Can’t reach Flight Watch</h2>
		<p class="muted">
			The server isn’t answering. Check your connection; if it’s fine, the server may be restarting.
		</p>
	{:else if status === 404}
		<h2>{notFound}</h2>
		<p class="muted">It may have been removed, or the link is wrong.</p>
	{:else if status === 503}
		<h2>No prices yet</h2>
		<p class="muted">
			Flight Watch is starting. The first ones arrive within a few minutes of adding a trip.
		</p>
	{:else}
		<h2>Something went wrong</h2>
		<p class="muted">{error instanceof Error ? error.message : String(error)}</p>
	{/if}
	<p class="actions">
		{#if retry && status !== 404}<button class="btn primary" onclick={retry}>Try again</button>{/if}
		<a class="btn" href={resolve('/')}>Overview</a>
	</p>
</div>

<style>
	.note {
		max-width: 560px;
	}
	.note h2 {
		font-size: 1.15rem;
	}
	.note p {
		margin: 0 0 10px;
	}
	.actions {
		display: flex;
		gap: 8px;
		margin: 14px 0 0 !important;
	}
</style>
