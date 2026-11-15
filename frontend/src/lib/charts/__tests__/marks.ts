// Shared checks for chart tests: every data mark stays inside its plot area.
import { expect } from 'vitest';

/** Points visited by an SVG path (M/L/H/V/Q/C/A/Z, and relative m/l/h/v). */
export function pathPoints(d: string): [number, number][] {
	const pts: [number, number][] = [];
	let x = 0;
	let y = 0;
	for (const [, cmd, args] of d.matchAll(/([MLHVQCAZmlhvz])([^MLHVQCAZmlhvz]*)/g)) {
		const n = (args.match(/-?\d*\.?\d+(e-?\d+)?/g) ?? []).map(Number);
		if (cmd === 'H') n.forEach((v) => pts.push([(x = v), y]));
		else if (cmd === 'V') n.forEach((v) => pts.push([x, (y = v)]));
		else if (cmd === 'h') n.forEach((v) => pts.push([(x += v), y]));
		else if (cmd === 'v') n.forEach((v) => pts.push([x, (y += v)]));
		else if (cmd === 'm' || cmd === 'l')
			for (let i = 0; i + 1 < n.length; i += 2) pts.push([(x += n[i]), (y += n[i + 1])]);
		else if (cmd === 'A')
			// rx ry rotation large-arc sweep x y: only the end point is a position.
			for (let i = 0; i + 6 < n.length; i += 7) pts.push([(x = n[i + 5]), (y = n[i + 6])]);
		else if (cmd !== 'Z' && cmd !== 'z')
			for (let i = 0; i + 1 < n.length; i += 2) pts.push([(x = n[i]), (y = n[i + 1])]);
	}
	return pts;
}

/** Assert every `.mark` inside each plot group lies within [0, iw] × [0, ih] (+ slack):
 * nothing is drawn outside the plot. */
export function expectMarksInside(container: HTMLElement, slack = 1) {
	const plots = container.querySelectorAll<SVGGElement>('g.plot');
	expect(plots.length).toBeGreaterThan(0);
	let checked = 0;
	plots.forEach((g) => {
		const iw = Number(g.dataset.iw);
		const ih = Number(g.dataset.ih);
		g.querySelectorAll<SVGElement>('.mark').forEach((el) => {
			const pts: [number, number][] = [];
			const a = (n: string) => Number(el.getAttribute(n));
			if (el.tagName === 'path') pts.push(...pathPoints(el.getAttribute('d') ?? ''));
			if (el.tagName === 'rect')
				pts.push([a('x'), a('y')], [a('x') + a('width'), a('y') + a('height')]);
			if (el.tagName === 'line') pts.push([a('x1'), a('y1')], [a('x2'), a('y2')]);
			if (el.tagName === 'circle') pts.push([a('cx'), a('cy')]);
			const title = g.closest('figure')?.querySelector('.title')?.textContent;
			for (const [x, y] of pts) {
				const where = `${title}: <${el.tagName} class="${el.getAttribute('class')}"> at (${x.toFixed(1)}, ${y.toFixed(1)}) in ${iw}×${ih}`;
				expect(x, where).toBeGreaterThanOrEqual(-slack);
				expect(x, where).toBeLessThanOrEqual(iw + slack);
				expect(y, where).toBeGreaterThanOrEqual(-slack);
				expect(y, where).toBeLessThanOrEqual(ih + slack);
				checked++;
			}
		});
	});
	expect(checked).toBeGreaterThan(0);
}
