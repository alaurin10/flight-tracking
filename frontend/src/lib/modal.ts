// Popups that behave like proper dialogs. Put the scrim and the sheet inside one
// wrapper and give it `use:modal={{ onclose }}`. While it's open:
// - everything else on the page is inert (each sibling along its ancestor chain), so
//   screen readers and the Tab key stay inside; the popup itself stays where Svelte
//   put it, so Svelte can remove it normally;
// - Tab and Shift+Tab cycle through its controls; the first one gets focus;
// - Escape closes only the topmost popup (one can open over another);
// - the page behind doesn't scroll;
// - when it closes, focus goes back to whatever opened it.

interface ModalOptions {
	onclose: () => void;
}

const FOCUSABLE =
	'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const stack: HTMLElement[] = [];

export function modal(node: HTMLElement, options: ModalOptions) {
	let opts = options;
	const opener = document.activeElement as HTMLElement | null;

	const made: Element[] = [];
	for (let el: Element | null = node; el && el !== document.body; el = el.parentElement) {
		for (const sib of el.parentElement?.children ?? []) {
			if (sib !== el && !sib.hasAttribute('inert') && !(sib instanceof HTMLScriptElement)) {
				sib.setAttribute('inert', '');
				made.push(sib);
			}
		}
	}
	stack.push(node);
	const overflow = document.body.style.overflow;
	document.body.style.overflow = 'hidden';

	const focusables = () =>
		[...node.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
			(el) => el.offsetParent !== null || el === document.activeElement
		);
	queueMicrotask(() => (focusables()[0] ?? node).focus());

	const onkey = (e: KeyboardEvent) => {
		if (stack[stack.length - 1] !== node) return;
		if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			opts.onclose();
		} else if (e.key === 'Tab') {
			const items = focusables();
			if (!items.length) return;
			const first = items[0];
			const last = items[items.length - 1];
			if (e.shiftKey && document.activeElement === first) {
				e.preventDefault();
				last.focus();
			} else if (!e.shiftKey && document.activeElement === last) {
				e.preventDefault();
				first.focus();
			}
		}
	};
	document.addEventListener('keydown', onkey, true);

	return {
		update(next: ModalOptions) {
			opts = next;
		},
		destroy() {
			document.removeEventListener('keydown', onkey, true);
			stack.splice(stack.indexOf(node), 1);
			for (const el of made) el.removeAttribute('inert');
			document.body.style.overflow = overflow;
			if (opener?.isConnected) opener.focus();
		}
	};
}
