<!-- Theme, what Flight Watch is, and (from the Manage milestone) the owner's sign-in. -->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { app } from '../../lib/app.svelte';
	import { prefs, type Theme } from '../../lib/prefs.svelte';

	const THEMES: { id: Theme; label: string }[] = [
		{ id: 'auto', label: 'Auto' },
		{ id: 'light', label: 'Light' },
		{ id: 'dark', label: 'Dark' }
	];
	const appVersion = __APP_VERSION__;
	const stale = $derived(!!app.meta && app.meta.version !== appVersion);
</script>

<svelte:head><title>More · Flight Watch</title></svelte:head>

<header class="pagehead">
	<h1>More</h1>
</header>

<div class="stack">
	<section class="card" aria-labelledby="theme-h">
		<h2 id="theme-h">Theme</h2>
		<div class="seg" role="group" aria-label="Colour theme">
			{#each THEMES as t (t.id)}
				<button aria-pressed={prefs.theme === t.id} onclick={() => prefs.setTheme(t.id)}
					>{t.label}</button
				>
			{/each}
		</div>
		<p class="faint small">Auto follows this device’s light or dark setting.</p>
	</section>

	<section class="card about" aria-labelledby="about-h">
		<h2 id="about-h">About Flight Watch</h2>
		<p>
			Flight Watch checks the prices of a few trips and flexible searches on Google Flights a few
			times a day, from a computer at home, and keeps every price it sees. Each trip gets a verdict:
		</p>
		<ul>
			<li><strong>BOOK</strong>: at or under the target, or at the lowest price seen.</li>
			<li><strong>LEAN BOOK</strong>: cheaper than usual for the route.</li>
			<li><strong>HOLD</strong>: an ordinary price; no reason to rush.</li>
			<li><strong>WAIT</strong>: still falling, or departure is far off.</li>
		</ul>
		<p>
			The verdicts are arithmetic on the price history, not a forecast. Prices marked
			<em>estimate</em> come from Google’s price calendar, which can include basic economy; the others
			are fares read from the results page. Deals come from public deal feeds, matched against deal watches.
		</p>
		<p class="faint small">
			Not affiliated with Google. Nothing here books anything: “Open in Google Flights” takes you
			there.
		</p>
	</section>

	<section class="card" aria-labelledby="owner-h">
		<h2 id="owner-h">Owner</h2>
		{#if app.meta && !app.meta.owner_login_enabled}
			<p class="muted">Owner sign-in isn’t set up on this server.</p>
		{:else}
			<p class="muted">
				Anyone with the link can read this site. Changing what is tracked needs the owner’s
				password; the sign-in and the Manage page arrive in the next version.
			</p>
		{/if}
	</section>

	<section class="card" aria-labelledby="ver-h">
		<h2 id="ver-h">Version</h2>
		<dl class="kv">
			<div>
				<dt>App</dt>
				<dd>{appVersion}</dd>
			</div>
			<div>
				<dt>Server</dt>
				<dd>
					{app.meta ? app.meta.version : '–'}{app.meta?.commit
						? ` (${app.meta.commit.slice(0, 7)})`
						: ''}
				</dd>
			</div>
			<div>
				<dt>Home airport</dt>
				<dd>{app.meta?.home_airport ?? '–'}</dd>
			</div>
		</dl>
		{#if stale}
			<p class="small faint">
				The server runs a different version: reload the page to get the newest app.
			</p>
		{/if}
		<p class="small"><a href={resolve('/status')}>Collector status</a></p>
	</section>
</div>

<style>
	.stack {
		display: grid;
		gap: 14px;
		max-width: 720px;
	}
	.small {
		font-size: 0.82rem;
		margin: 10px 0 0;
	}
	.about p {
		margin: 0 0 10px;
	}
	.about ul {
		margin: 0 0 10px;
		padding-left: 1.2em;
	}
</style>
