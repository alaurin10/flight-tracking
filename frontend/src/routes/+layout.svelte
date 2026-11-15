<!-- The app shell, after Snow Watch's: on desktop a glass rail on the left holds the
     navigation and every trip and search with its price now; on phones and tablets a
     floating glass tab bar sits at the bottom. Notices (stale prices, no prices yet,
     the collector stopped, the server unreachable) float at the top as a toast. -->
<script lang="ts">
	import '@fontsource/inter/400.css';
	import '@fontsource/inter/500.css';
	import '@fontsource/inter/600.css';
	import '@fontsource/manrope/500.css';
	import '@fontsource/manrope/600.css';
	import '@fontsource/manrope/700.css';
	import '../lib/theme.css';
	import favicon from '../lib/assets/favicon.svg';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { prefs } from '../lib/prefs.svelte';
	import { app } from '../lib/app.svelte';
	import { ApiError, getHealth, getOverview } from '../lib/api';
	import { money } from '../lib/format';
	import type { HealthOut, OverviewOut } from '../lib/types.gen';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();
	let health = $state<HealthOut | null>(null);
	let unreachable = $state(false);
	let online = $state(true);
	let overview = $state<OverviewOut | null>(null);
	let closed = $state<string | null>(null);

	// Colour mode lives on <html> (static/theme-init.js sets it before the app starts);
	// this keeps it in step with the More page and the device.
	$effect(() => {
		const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
		if (!mq) return;
		const on = (e: MediaQueryListEvent) => (prefs.systemDark = e.matches);
		mq.addEventListener('change', on);
		return () => mq.removeEventListener('change', on);
	});
	$effect(() => {
		document.documentElement.dataset.mode = prefs.dark ? 'dark' : 'light';
	});

	$effect(() => {
		online = navigator.onLine;
		const on = () => (online = true);
		const off = () => (online = false);
		window.addEventListener('online', on);
		window.addEventListener('offline', off);
		const check = () =>
			getHealth()
				.then((h) => {
					health = h;
					unreachable = false;
				})
				.catch((e: unknown) => {
					unreachable = e instanceof ApiError && e.unreachable;
				});
		check();
		const timer = setInterval(check, 5 * 60 * 1000);
		return () => {
			window.removeEventListener('online', on);
			window.removeEventListener('offline', off);
			clearInterval(timer);
		};
	});

	// Every trip and search for the rail (the overview's cached copy).
	$effect(() => {
		void page.url.pathname; // refresh on navigation (the cache keeps it to once a minute)
		getOverview()
			.then((o) => (overview = o))
			.catch(() => {});
	});

	const path = $derived(page.url.pathname);
	const ICONS = {
		overview: 'M4 6h16M4 12h16M4 18h10',
		// A price tag.
		deals: 'M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8zM7.5 7.5h.01',
		// A pulse line.
		status: 'M3 12h4l3-8 4 16 3-8h4',
		// Sliders.
		manage: 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4',
		more: 'M5 12h.01M12 12h.01M19 12h.01'
	};
	const tabs = [
		{ href: '/', label: 'Overview', icon: ICONS.overview },
		{ href: '/deals', label: 'Deals', icon: ICONS.deals },
		{ href: '/status', label: 'Status', icon: ICONS.status },
		{ href: '/manage', label: 'Manage', icon: ICONS.manage },
		{ href: '/more', label: 'More', icon: ICONS.more }
	] as const;
	const active = (href: string) =>
		href === '/'
			? path === '/' || path.startsWith('/trip') || path.startsWith('/search')
			: path.startsWith(href);

	const staleHours = $derived(
		health?.status === 'stale' && health.age_s ? Math.round(health.age_s / 3600) : null
	);
	// One notice at a time, most pressing first.
	const notice = $derived(
		!online
			? 'offline'
			: unreachable
				? 'unreachable'
				: health?.status === 'worker_stopped'
					? 'stopped'
					: staleHours !== null
						? 'stale'
						: health?.status === 'no_data'
							? 'nodata'
							: null
	);
	const warn = $derived(notice === 'stale' || notice === 'stopped' || notice === 'unreachable');
	const currency = $derived(app.currency);
	$effect(() => app.load());
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<meta name="theme-color" content={prefs.dark ? '#1a2336' : '#e9f0f8'} />
</svelte:head>

<div class="app">
	<aside class="rail glass" aria-label="Navigation">
		<a class="brand" href={resolve('/')}>
			<span class="mark" aria-hidden="true">
				<svg viewBox="0 0 24 24" width="18" height="18"
					><path d="M2 14.5l20-8-6 10.5-3.5-4.5-4.5 2.5-2-2 4-2.5z" /></svg
				>
			</span>
			Flight Watch
		</a>
		<nav class="railnav" aria-label="Main">
			{#each tabs as t (t.href)}
				<a
					href={resolve(t.href)}
					aria-current={(t.href === '/' ? path === '/' : active(t.href)) ? 'page' : undefined}
				>
					<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"
						><path d={t.icon} /></svg
					>
					{t.label}
				</a>
			{/each}
		</nav>
		{#if overview?.trips.length}
			<div class="railhead">Trips</div>
			<ul class="items">
				{#each overview.trips as t (t.slug)}
					{@const href = resolve('/trip/[slug]', { slug: t.slug })}
					<li>
						<a class="item" {href} aria-current={path === href ? 'page' : undefined}>
							<span class="iname">{t.name}</span>
							<span class="iprice num">{money(t.price, currency)}</span>
						</a>
					</li>
				{/each}
			</ul>
		{/if}
		{#if overview?.searches.length}
			<div class="railhead">Flexible searches</div>
			<ul class="items">
				{#each overview.searches as s (s.slug)}
					{@const href = resolve('/search/[slug]', { slug: s.slug })}
					<li>
						<a class="item" {href} aria-current={path === href ? 'page' : undefined}>
							<span class="iname">{s.name}</span>
							<span class="iprice num">{money(s.best?.price, currency)}</span>
						</a>
					</li>
				{/each}
			</ul>
		{/if}
	</aside>

	<div class="col">
		{#if notice && closed !== notice}
			<div class="banner" class:glass={!warn} class:warn role="status">
				<span class="btxt">
					{#if notice === 'offline'}
						You’re offline. Prices will refresh when you’re back.
					{:else if notice === 'unreachable'}
						Can’t reach Flight Watch right now.
					{:else if notice === 'stopped'}
						The collector has stopped. <a href={resolve('/status')}>Status</a>
					{:else if notice === 'stale'}
						Prices haven’t updated for {staleHours} hours.
						<a href={resolve('/status')}>Status</a>
					{:else}
						No prices yet. The first ones arrive within a few minutes of adding a trip.
					{/if}
				</span>
				<button class="bclose" onclick={() => (closed = notice)} aria-label="Close notice">
					<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"
						><path d="M6 6l12 12M18 6L6 18" /></svg
					>
				</button>
			</div>
		{/if}

		<main>
			{@render children()}
		</main>

		<footer>
			<span class="glass"
				>Prices from Google Flights, checked a few times a day; the verdicts are arithmetic on that
				history, not a forecast. <a href={resolve('/more')}>About</a></span
			>
		</footer>
	</div>
</div>

<nav class="tabbar glass" aria-label="Main">
	{#each tabs as t (t.href)}
		<a href={resolve(t.href)} aria-current={active(t.href) ? 'page' : undefined}>
			<svg viewBox="0 0 24 24" width="21" height="21" aria-hidden="true"><path d={t.icon} /></svg>
			<span>{t.label}</span>
		</a>
	{/each}
</nav>

<style>
	.app {
		min-height: 100vh;
	}
	.col {
		min-width: 0;
	}
	.rail {
		display: none;
	}

	/* --- Desktop: the rail, fixed at the top left; the page column beside it --- */
	@media (min-width: 1024px) {
		.app {
			padding-top: 14px;
		}
		.rail {
			position: fixed;
			top: 14px;
			left: 14px;
			z-index: 20;
			width: 248px;
			max-height: calc(100vh - 28px);
			overflow-y: auto;
			border-radius: 28px;
			padding: 14px 10px 12px;
			display: flex;
			flex-direction: column;
			gap: 4px;
			scrollbar-width: thin;
		}
		.col {
			max-width: 1080px;
			margin-left: max(282px, calc(282px + (100vw - 302px - 1080px) / 2));
			margin-right: 20px;
		}
		.tabbar {
			display: none !important;
		}
		main {
			padding: 4px 0 24px !important;
		}
		footer {
			padding: 8px 0 28px !important;
		}
		.banner {
			top: 14px !important;
			left: calc(272px + (100vw - 272px) / 2) !important;
			max-width: min(560px, calc(100vw - 300px)) !important;
		}
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 2px 8px 10px;
		font-family: var(--font-num);
		font-weight: 700;
		font-size: 1.08rem;
		color: var(--text);
		text-decoration: none;
		letter-spacing: -0.01em;
	}
	.mark {
		width: 32px;
		height: 32px;
		border-radius: 11px;
		background: var(--accent);
		display: grid;
		place-items: center;
	}
	.mark svg {
		fill: none;
		stroke: var(--on-accent);
		stroke-width: 1.8;
		stroke-linejoin: round;
	}
	.railnav {
		display: grid;
		gap: 2px;
	}
	.railnav a,
	.item {
		display: flex;
		align-items: center;
		gap: 10px;
		min-height: 40px;
		padding: 0 10px;
		border-radius: 14px;
		font-weight: 500;
		color: var(--text-2);
		text-decoration: none;
		transition: background-color var(--ease);
	}
	.railnav a:hover,
	.item:hover {
		background: var(--hover);
	}
	.railnav a[aria-current='page'],
	.item[aria-current='page'] {
		background: var(--sel);
		box-shadow: var(--sel-shadow);
		color: var(--text);
	}
	.railnav svg {
		fill: none;
		stroke: currentColor;
		stroke-width: 1.9;
		stroke-linecap: round;
		stroke-linejoin: round;
		flex-shrink: 0;
	}
	.railhead {
		padding: 14px 10px 4px;
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--text-3);
	}
	.items {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 2px;
	}
	.item {
		color: var(--text);
	}
	.iname {
		flex: 1;
		min-width: 0;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.iprice {
		font-weight: 600;
	}

	/* --- Notices: a toast at the top, over the page --- */
	.banner {
		position: fixed;
		top: calc(10px + env(safe-area-inset-top));
		left: 50%;
		transform: translateX(-50%);
		z-index: 40;
		width: max-content;
		max-width: min(560px, calc(100vw - 32px));
		padding: 6px 6px 6px 16px;
		border-radius: 18px;
		font-size: 0.9rem;
		display: flex;
		align-items: center;
		gap: 8px;
		box-shadow: var(--glass-shadow);
	}
	.banner.glass {
		background-color: color-mix(in srgb, var(--surface) 92%, transparent);
	}
	.banner.warn {
		background: var(--warn-bg);
		border: 1px solid var(--warn-border);
		color: var(--warn-ink);
	}
	.banner a {
		color: inherit;
	}
	.btxt {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
		min-width: 0;
		padding: 4px 0;
	}
	.bclose {
		flex: 0 0 32px;
		height: 32px;
		display: grid;
		place-items: center;
		border: 0;
		border-radius: 12px;
		background: transparent;
		color: inherit;
		cursor: pointer;
	}
	.bclose:hover {
		background: var(--hover);
	}
	.bclose svg {
		fill: none;
		stroke: currentColor;
		stroke-width: 2;
		stroke-linecap: round;
	}

	main {
		max-width: 1080px;
		margin: 0 auto;
		padding: 12px 16px 24px;
		padding-top: calc(12px + env(safe-area-inset-top));
	}
	footer {
		max-width: 1080px;
		margin: 0 auto;
		padding: 8px 16px 120px;
		font-size: 0.78rem;
		color: var(--text-2);
	}
	footer span {
		display: inline-block;
		padding: 8px 14px;
		border-radius: 16px;
	}
	footer a {
		color: inherit;
	}

	/* --- Phone and tablet tab bar --- */
	.tabbar {
		position: fixed;
		left: 16px;
		right: 16px;
		bottom: calc(14px + env(safe-area-inset-bottom));
		z-index: 30;
		max-width: 440px;
		margin: 0 auto;
		display: grid;
		grid-template-columns: repeat(5, 1fr);
		gap: 2px;
		padding: 5px;
		border-radius: 999px;
	}
	.tabbar a {
		display: grid;
		justify-items: center;
		align-content: center;
		gap: 2px;
		min-height: 46px;
		border-radius: 999px;
		color: var(--text-2);
		text-decoration: none;
		font-size: 0.7rem;
		font-weight: 500;
		transition: background-color var(--ease);
	}
	.tabbar svg {
		fill: none;
		stroke: currentColor;
		stroke-width: 1.9;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.tabbar a[aria-current='page'] {
		color: var(--text);
		background: var(--sel);
		box-shadow: var(--sel-shadow);
	}
</style>
