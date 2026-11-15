import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/svelte';
import search from '../fixtures/search.json';
import type { DatePairOut, SearchOut } from '../types.gen';
import PriceCalendar from './PriceCalendar.svelte';

const pairs = (search as unknown as SearchOut).grid[0].pairs;

afterEach(() => cleanup());

describe('price calendar', () => {
	it('has one button per date pair, the cheapest outlined', () => {
		const { container } = render(PriceCalendar, { pairs, dest: 'SLC' });
		expect(container.querySelectorAll('button.cell')).toHaveLength(pairs.length);
		const best = container.querySelectorAll('button.cell.best');
		expect(best).toHaveLength(1);
		const cheapest = pairs.find((p) => p.flags.includes('cheapest'))!;
		expect(best[0].getAttribute('data-date')).toBe(cheapest.depart_date);
		expect(best[0].getAttribute('aria-label')).toContain('cheapest');
		expect(container.querySelectorAll('button.cell.p1').length).toBeGreaterThan(0);
		expect(container.querySelectorAll('button.cell.p4').length).toBeGreaterThan(0);
	});

	it('marks estimates, targets and lows, and says not priced', () => {
		const base = pairs.slice(0, 3);
		const marked: DatePairOut[] = [
			{ ...base[0], source: 'calendar', flags: ['estimate'] },
			{ ...base[1], flags: ['under_target'] },
			{ ...base[2], price: null, source: null, flags: [] }
		];
		const { container } = render(PriceCalendar, { pairs: marked, dest: 'SLC' });
		const cells = container.querySelectorAll('button.cell');
		expect(cells[0].classList.contains('est')).toBe(true);
		expect(cells[0].getAttribute('aria-label')).toContain('calendar estimate');
		expect(cells[1].textContent).toContain('★');
		expect(cells[2].classList.contains('none')).toBe(true);
		expect(cells[2].getAttribute('aria-label')).toContain('not priced yet');
	});

	it('tells the page which date was tapped', async () => {
		const onselect = vi.fn();
		const { container } = render(PriceCalendar, { pairs, dest: 'SLC', onselect });
		await fireEvent.click(container.querySelectorAll('button.cell')[3]);
		expect(onselect).toHaveBeenCalledWith(pairs[3]);
	});
});
