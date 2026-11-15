// Every chart rendered from real API answers (the demo database), at a phone's width
// and a desktop's: no data mark falls outside its plot, and each chart's table twin
// holds the same numbers.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/svelte';
import { expectMarksInside } from './marks';
import trip from '../../fixtures/trip.json';
import status from '../../fixtures/status.json';
import type { StatusOut, TripOut } from '../../types.gen';
import PriceChart from '../PriceChart.svelte';
import RequestsChart from '../RequestsChart.svelte';
import Sparkline from '../Sparkline.svelte';

const t = trip as unknown as TripOut;
const s = status as unknown as StatusOut;
const dollars = (cents: number) => `$${(cents / 100).toLocaleString('en-US')}`;

afterEach(() => cleanup());

describe.each([320, 900])('at %i px', (width) => {
	it('price chart keeps every mark inside, the target included', () => {
		const { container } = render(PriceChart, {
			props: {
				points: t.series,
				target: t.target_price,
				width
			}
		});
		expectMarksInside(container);
		expect(container.querySelector('.target')).not.toBeNull();
	});

	it('price chart with a target far below every price still draws it inside', () => {
		const { container } = render(PriceChart, { props: { points: t.series, target: 10000, width } });
		expectMarksInside(container);
	});

	it('requests chart keeps every bar inside', () => {
		const { container } = render(RequestsChart, {
			props: {
				days: s.requests_by_day,
				budget: s.budget.limit,
				width
			}
		});
		expectMarksInside(container);
		expect(container.querySelectorAll('.bar').length).toBeGreaterThanOrEqual(
			s.requests_by_day.length
		);
	});

	it('sparkline keeps every mark inside', () => {
		const { container } = render(Sparkline, {
			props: {
				points: t.series.slice(-30),
				target: 80000,
				width
			}
		});
		expectMarksInside(container);
	});
});

describe('price chart', () => {
	it('has a table twin with one row per check, newest first, matching the line', () => {
		const { container } = render(PriceChart, {
			props: { points: t.series, target: 80000, width: 600 }
		});
		const line = container.querySelector('path.line')!;
		const rows = container.querySelectorAll('tbody tr[data-kind="price"]');
		expect(Number(line.getAttribute('data-n'))).toBe(t.series.length);
		expect(rows).toHaveLength(t.series.length);
		const newest = t.series[t.series.length - 1].price;
		expect(rows[0].querySelector('.num')?.textContent).toBe(dollars(newest));
		expect(container.querySelector('.readout')?.textContent).toContain(dollars(newest));
	});

	it('marks calendar estimates apart from checked prices', () => {
		const { container } = render(PriceChart, {
			props: {
				points: t.series.slice(0, 5),
				calendar: [{ at: t.series[2].at, price: 70000 }],
				width: 600
			}
		});
		expect(container.querySelectorAll('circle.est')).toHaveLength(1);
		expect(container.querySelectorAll('tr[data-kind="estimate"]')).toHaveLength(1);
		expect(container.textContent).toContain('calendar estimate');
		expectMarksInside(container);
	});

	it('moves the crosshair with the arrow keys', async () => {
		const { container } = render(PriceChart, { props: { points: t.series, width: 600 } });
		const svg = container.querySelector('svg')!;
		expect(svg.getAttribute('tabindex')).toBe('0');
		await fireEvent.keyDown(svg, { key: 'Home' });
		expect(container.querySelector('.readout')?.textContent).toContain(dollars(t.series[0].price));
		expect(container.querySelector('line.cross')).not.toBeNull();
		await fireEvent.keyDown(svg, { key: 'ArrowRight' });
		expect(container.querySelector('.readout')?.textContent).toContain(dollars(t.series[1].price));
		await fireEvent.keyDown(svg, { key: 'Escape' });
		expect(container.querySelector('line.cross')).toBeNull();
	});

	it('says so when there are no prices', () => {
		const { container } = render(PriceChart, { props: { points: [], width: 400 } });
		expect(container.textContent).toContain('No prices yet.');
		expect(container.querySelector('details')).toBeNull();
	});

	it('draws a single check in the middle', () => {
		const { container } = render(PriceChart, { props: { points: t.series.slice(-1), width: 400 } });
		const end = container.querySelector('circle.end')!;
		const iw = Number(container.querySelector('g.plot')!.getAttribute('data-iw'));
		expect(Number(end.getAttribute('cx'))).toBeCloseTo(iw / 2, 0);
		expectMarksInside(container);
	});
});

describe('requests chart', () => {
	it('has a table with every day and outcome', () => {
		const { container } = render(RequestsChart, { props: { days: s.requests_by_day, width: 600 } });
		expect(container.querySelectorAll('tbody tr')).toHaveLength(s.requests_by_day.length);
		expect(container.querySelector('thead')?.textContent).toContain('unreadable page');
	});
});
