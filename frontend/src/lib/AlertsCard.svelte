<!-- More → Alerts: browser push on this device for the owner. Shows why it can't be
     turned on (not set up on the server, not signed in, an iPhone outside the Home Screen
     app, notifications blocked); otherwise the kinds to get, Turn on, Send a test, Turn off. -->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { app } from './app.svelte';
	import {
		KINDS,
		pushSupport,
		saveKinds,
		savedKinds,
		sendTest,
		serverKey,
		turnOff,
		turnOn,
		type Kind,
		type PushSupport
	} from './push';
	import { owner } from './session.svelte';

	let support = $state<PushSupport>('unsupported');
	let on = $state(false);
	let kinds = $state<Kind[]>(['price', 'deal', 'health']);
	let busy = $state(false);
	let note = $state<{ text: string; bad?: boolean } | null>(null);

	const ready = $derived(!!app.meta?.push_enabled && owner.isOwner);

	$effect(() => {
		support = pushSupport();
		if (!ready || support !== 'ok') return;
		savedKinds()
			.then((k) => {
				on = !!k;
				if (k) kinds = k;
			})
			.catch(() => {});
	});

	function toggle(k: Kind) {
		kinds = kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k];
	}

	async function act(work: () => Promise<{ ok: boolean; detail?: string | null }>, done: string) {
		busy = true;
		note = null;
		try {
			const r = await work();
			note = r.ok ? { text: done } : { text: r.detail ?? 'That didn’t work.', bad: true };
			return r.ok;
		} catch (e) {
			note = { text: e instanceof Error ? e.message : String(e), bad: true };
			return false;
		} finally {
			busy = false;
		}
	}

	async function enable() {
		const key = await serverKey();
		if (!key) {
			note = { text: 'Push alerts aren’t set up on the server.', bad: true };
			return;
		}
		if (await act(() => turnOn(key, kinds), 'Alerts are on for this device.')) on = true;
	}
	async function disable() {
		if (await act(turnOff, 'Alerts are off for this device.')) on = false;
	}
</script>

<section class="card" aria-labelledby="alerts-h" data-testid="alerts">
	<h2 id="alerts-h">Alerts</h2>
	{#if app.meta && !app.meta.push_enabled}
		<p class="muted">
			Push alerts are not set up on the server. Alerts are still recorded and shown on the overview.
			To send them to your phone, run <code>flighttrack push keys</code> and add the two lines to
			<code>.env</code> (see the deployment notes).
		</p>
	{:else if !owner.isOwner}
		<p class="muted">Alerts go to the owner’s devices.</p>
		<p class="small"><a class="btn" href={resolve('/manage')}>Sign in</a></p>
	{:else if support === 'ios-home-screen'}
		<p class="muted">
			On an iPhone or iPad, add Flight Watch to the Home Screen first (Share → Add to Home Screen),
			open it from there, and turn alerts on in that app. It needs iOS 16.4 or later.
		</p>
	{:else if support === 'denied'}
		<p class="muted">
			Notifications are blocked for this site in the browser’s settings. Allow them there, then come
			back.
		</p>
	{:else if support === 'unsupported'}
		<p class="muted">This browser can’t receive push alerts.</p>
	{:else}
		<fieldset class="kinds">
			<legend class="muted">Send me</legend>
			{#each KINDS as k (k.id)}
				<label class="kind">
					<input
						type="checkbox"
						checked={kinds.includes(k.id)}
						onchange={() => toggle(k.id)}
						disabled={busy}
					/>
					<span><strong>{k.label}</strong> <span class="faint">{k.hint}</span></span>
				</label>
			{/each}
		</fieldset>
		<p class="acts">
			{#if on}
				<button
					class="btn"
					disabled={busy || !kinds.length}
					onclick={() => act(() => saveKinds(kinds), 'Saved.')}>Save</button
				>
				<button class="btn" disabled={busy} onclick={() => act(sendTest, 'Test sent.')}
					>Send a test</button
				>
				<button class="btn" disabled={busy} onclick={disable}>Turn off</button>
			{:else}
				<button class="btn primary" disabled={busy || !kinds.length} onclick={enable}
					>Turn on alerts on this device</button
				>
			{/if}
		</p>
	{/if}
	<div aria-live="polite">
		{#if note}<p class="notice {note.bad ? 'bad' : 'info'}">{note.text}</p>{/if}
	</div>
</section>

<style>
	.kinds {
		border: 0;
		margin: 0 0 10px;
		padding: 0;
		display: grid;
		gap: 8px;
	}
	.kind {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		min-height: 40px;
	}
	.kind input {
		margin-top: 3px;
		width: 18px;
		height: 18px;
		accent-color: var(--accent);
	}
	.acts {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin: 0;
	}
	code {
		font-size: 0.9em;
	}
</style>
