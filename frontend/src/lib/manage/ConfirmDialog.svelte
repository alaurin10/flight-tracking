<!-- "Are you sure?" as a proper dialog (focus kept inside, Escape cancels, the page behind
     inert), using modal.ts from Snow Watch. -->
<script lang="ts">
	import { modal } from '../modal';

	interface Props {
		title: string;
		body: string;
		confirm: string;
		danger?: boolean;
		onconfirm: () => void;
		oncancel: () => void;
	}
	let { title, body, confirm, danger = false, onconfirm, oncancel }: Props = $props();
</script>

<div class="wrap" use:modal={{ onclose: oncancel }}>
	<div class="scrim" aria-hidden="true" onclick={oncancel}></div>
	<div
		class="sheet card"
		role="alertdialog"
		aria-modal="true"
		aria-labelledby="confirm-title"
		aria-describedby="confirm-body"
	>
		<h2 id="confirm-title">{title}</h2>
		<p id="confirm-body" class="muted">{body}</p>
		<div class="actions">
			<button type="button" class="btn" onclick={oncancel}>Cancel</button>
			<button type="button" class="btn primary" class:danger onclick={onconfirm}>{confirm}</button>
		</div>
	</div>
</div>

<style>
	.wrap {
		position: fixed;
		inset: 0;
		z-index: 60;
		display: grid;
		place-items: end center;
		padding: 16px;
		padding-bottom: calc(16px + env(safe-area-inset-bottom));
	}
	@media (min-width: 640px) {
		.wrap {
			place-items: center;
		}
	}
	.scrim {
		position: absolute;
		inset: 0;
		background: rgba(8, 16, 30, 0.45);
	}
	.sheet {
		position: relative;
		width: min(440px, 100%);
		background: var(--surface);
		backdrop-filter: none;
	}
	h2 {
		font-size: 1.1rem;
	}
	p {
		margin: 0 0 16px;
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
	}
	.actions .btn {
		min-height: 44px;
		padding: 8px 18px;
	}
	.btn.danger {
		background: var(--warn);
	}
</style>
