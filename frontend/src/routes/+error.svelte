<!-- An address that isn't a page of the app. -->
<script lang="ts">
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
</script>

<svelte:head><title>Not found · Flight Watch</title></svelte:head>

<div class="card note">
	{#if page.status === 404}
		<h1>There’s nothing here</h1>
		<p class="muted">This address isn’t a page of Flight Watch.</p>
	{:else}
		<h1>Something went wrong</h1>
		<p class="muted">{page.error?.message ?? 'The page couldn’t be shown.'}</p>
	{/if}
	<p><a class="btn primary" href={resolve('/')}>Overview</a></p>
</div>

<style>
	.note {
		max-width: 560px;
		margin-top: 18px;
	}
	h1 {
		font-size: 1.3rem;
		margin: 0 0 8px;
	}
</style>
