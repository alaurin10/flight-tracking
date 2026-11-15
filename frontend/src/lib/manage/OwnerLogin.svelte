<!-- The owner password form (copied from Snow Watch). -->
<script lang="ts">
	import { ApiError, login } from '../api';
	import type { Me } from '../types.gen';

	let { onSignedIn }: { onSignedIn: (me: Me) => void } = $props();

	let password = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!password || busy) return;
		busy = true;
		error = null;
		try {
			const me = await login(password);
			password = '';
			onSignedIn(me);
		} catch (err) {
			error =
				err instanceof ApiError && !err.unreachable
					? err.status === 401
						? 'That password isn’t right.'
						: err.message.charAt(0).toUpperCase() + err.message.slice(1) + '.'
					: 'Couldn’t reach Flight Watch. Check your connection and try again.';
		} finally {
			busy = false;
		}
	}
</script>

<form class="card form login" onsubmit={submit}>
	<h2>Owner sign-in</h2>
	<p class="muted">Enter the owner password set on the server to change what is tracked.</p>
	<div class="field">
		<label for="owner-password">Password</label>
		<input
			id="owner-password"
			type="password"
			autocomplete="current-password"
			bind:value={password}
			aria-invalid={error ? 'true' : undefined}
			aria-describedby={error ? 'owner-password-error' : undefined}
			required
		/>
		{#if error}<p id="owner-password-error" class="err" role="alert">{error}</p>{/if}
	</div>
	<div class="actions start">
		<button class="btn primary" type="submit" disabled={busy || !password}>
			{busy ? 'Signing in…' : 'Sign in'}
		</button>
	</div>
</form>

<style>
	.login {
		max-width: 440px;
	}
	h2,
	p {
		margin: 0;
	}
	.start {
		justify-content: flex-start;
	}
</style>
