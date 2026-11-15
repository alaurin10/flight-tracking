// Small geometry helpers shared by the charts. Pure, so they're easy to test.
import { line } from 'd3-shape';

export interface Margin {
	top: number;
	right: number;
	bottom: number;
	left: number;
}

/** A bar with rounded top corners, anchored on the baseline at y + h. */
export function roundedTopBar(x: number, y: number, w: number, h: number, r = 3): string {
	if (h <= 0 || w <= 0) return '';
	const rr = Math.min(r, w / 2, h);
	const b = y + h;
	return (
		`M${x},${b}V${y + rr}` +
		`Q${x},${y} ${x + rr},${y}` +
		`H${x + w - rr}` +
		`Q${x + w},${y} ${x + w},${y + rr}` +
		`V${b}Z`
	);
}

/** A line through (x, y) points, broken wherever y is null. */
export function linePath(xs: number[], ys: (number | null)[]): string {
	const pts = xs.map((x, i) => [x, ys[i]] as const);
	return (
		line<(typeof pts)[number]>()
			.defined((p) => p[1] !== null && Number.isFinite(p[1]))
			.x((p) => p[0])
			.y((p) => p[1] as number)(pts) ?? ''
	);
}

/** The area under a line down to the baseline `y0`. */
export function areaPath(xs: number[], ys: number[], y0: number): string {
	if (!xs.length) return '';
	return `${linePath(xs, ys)}L${xs.at(-1)},${y0}L${xs[0]},${y0}Z`;
}

export const finite = (vals: (number | null | undefined)[]): number[] =>
	vals.filter((v): v is number => v !== null && v !== undefined && Number.isFinite(v));

export const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/** The index of the value in a sorted list nearest to `v`. */
export function nearest(sorted: number[], v: number): number {
	if (!sorted.length) return -1;
	let lo = 0;
	let hi = sorted.length - 1;
	while (hi - lo > 1) {
		const mid = (lo + hi) >> 1;
		if (sorted[mid] < v) lo = mid;
		else hi = mid;
	}
	return Math.abs(sorted[lo] - v) <= Math.abs(sorted[hi] - v) ? lo : hi;
}

/** The y domain for prices: every value given (target included), padded so nothing sits
 * on the frame, rounded out to whole dollars. Cents in, cents out. */
export function priceDomain(values: (number | null | undefined)[]): [number, number] {
	const v = finite(values);
	if (!v.length) return [0, 100_00];
	let lo = Math.min(...v);
	let hi = Math.max(...v);
	if (hi === lo) {
		lo -= Math.max(2000, lo * 0.05);
		hi += Math.max(2000, hi * 0.05);
	}
	const pad = (hi - lo) * 0.08;
	return [Math.max(0, Math.floor((lo - pad) / 100) * 100), Math.ceil((hi + pad) / 100) * 100];
}
