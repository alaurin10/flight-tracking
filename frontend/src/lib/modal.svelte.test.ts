// Runs in jsdom (the ".svelte.test" name picks the browser test project).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { modal } from './modal';

afterEach(() => (document.body.innerHTML = ''));

function page() {
	document.body.innerHTML =
		'<main><button id="opener">Open</button><div id="slot"><div id="m"><button id="a">A</button><button id="b">B</button></div></div></main>';
	const opener = document.getElementById('opener') as HTMLButtonElement;
	opener.focus();
	return { opener, node: document.getElementById('m') as HTMLElement };
}

const key = (k: string, shift = false) =>
	document.dispatchEvent(new KeyboardEvent('keydown', { key: k, shiftKey: shift, bubbles: true }));

describe('modal', () => {
	it('makes the page inert, closes on Escape and gives focus back', async () => {
		const { opener, node } = page();
		const onclose = vi.fn();
		const m = modal(node, { onclose });
		await Promise.resolve();
		expect(document.getElementById('opener')?.hasAttribute('inert')).toBe(true);
		expect(node.closest('[inert]')).toBeNull();
		expect(document.body.style.overflow).toBe('hidden');
		key('Escape');
		expect(onclose).toHaveBeenCalledOnce();
		m.destroy();
		expect(document.querySelector('[inert]')).toBeNull();
		expect(document.activeElement).toBe(opener);
	});

	it('only the topmost popup closes on Escape', () => {
		const { node } = page();
		const second = document.createElement('div');
		document.body.append(second);
		const first = vi.fn();
		const top = vi.fn();
		const a = modal(node, { onclose: first });
		const b = modal(second, { onclose: top });
		key('Escape');
		expect(top).toHaveBeenCalledOnce();
		expect(first).not.toHaveBeenCalled();
		b.destroy();
		a.destroy();
	});
});
