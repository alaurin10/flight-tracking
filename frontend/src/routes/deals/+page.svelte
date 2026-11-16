<!-- Posts from the deal pages and feeds: price, headline, what it matched (if anything), link.
     "Matches only" keeps the posts that alerted for a region, trip or flexible search. -->
<script lang="ts">
	import { getDeals } from '../../lib/api';
	import { app } from '../../lib/app.svelte';
	import { ago, instant, money } from '../../lib/format';
	import { Resource } from '../../lib/resource.svelte';
	import type { DealOut } from '../../lib/types.gen';
	import ErrorNote from '../../lib/components/ErrorNote.svelte';
	import Loading from '../../lib/components/Loading.svelte';

	const deals = new Resource<DealOut[]>();
	$effect(() => deals.load(getDeals()));
	const cur = $derived(app.currency);
	let matchesOnly = $state(false);
	const shown = $derived((deals.data ?? []).filter((d) => !matchesOnly || d.watch));
	const host = (u: string) => {
		try {
			return new URL(u).host.replace(/^www\./, '');
		} catch {
			return u;
		}
	};
</script>

<svelte:head><title>Deals · Flight Watch</title></svelte:head>

<header class="pagehead">
	<h1>Deals</h1>
	<p>New posts from your deal pages. Those marked with a name matched something you watch.</p>
	<div class="seg filter" role="group" aria-label="Which posts">
		<button aria-pressed={!matchesOnly} onclick={() => (matchesOnly = false)}>All posts</button>
		<button aria-pressed={matchesOnly} onclick={() => (matchesOnly = true)}>Matches only</button>
	</div>
</header>

{#if deals.data}
	{#if shown.length}
		<ul class="card rows deals">
			{#each shown as d (d.id)}
				<li class="deal" data-testid="deal">
					<span class="price num">{money(d.price, cur)}</span>
					<div class="body">
						{#if d.link}
							<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an outside link -->
							<a href={d.link} target="_blank" rel="noopener noreferrer">{d.title}</a>
						{:else}
							<span>{d.title}</span>
						{/if}
						<p class="faint">
							{#if d.watch}<span class="pill good">{d.watch}</span>{/if}
							<span title={instant(d.published_at ?? d.seen_at)}
								>{ago(d.published_at ?? d.seen_at)}</span
							>
							· {host(d.feed)}
						</p>
					</div>
				</li>
			{/each}
		</ul>
	{:else}
		<div class="card">
			<p class="muted">
				{matchesOnly
					? 'No post has matched a region, trip or flexible search yet.'
					: 'No posts yet. Deal pages are read every few hours.'}
			</p>
		</div>
	{/if}
{:else if deals.error}
	<ErrorNote error={deals.error} retry={() => deals.load(getDeals())} />
{:else}
	<Loading />
{/if}

<style>
	.filter {
		margin-top: 12px;
	}
	.deals {
		margin: 0;
	}
	.deal {
		display: grid;
		grid-template-columns: 4.6rem minmax(0, 1fr);
		gap: 12px;
		align-items: start;
	}
	.price {
		font-size: 1.2rem;
		font-weight: 700;
	}
	.body {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.body a {
		font-weight: 500;
	}
	.body p {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 8px;
		margin: 4px 0 0;
		font-size: 0.8rem;
	}
</style>
