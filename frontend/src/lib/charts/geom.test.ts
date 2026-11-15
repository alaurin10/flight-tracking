import { describe, expect, it } from 'vitest';
import { areaPath, linePath, nearest, priceDomain, roundedTopBar } from './geom';

describe('chart geometry', () => {
	it('pads the price domain and always includes every value given', () => {
		const [lo, hi] = priceDomain([84300, 123900, 80000]);
		expect(lo).toBeLessThan(80000);
		expect(hi).toBeGreaterThan(123900);
		expect(lo % 100).toBe(0);
		expect(priceDomain([50000])[0]).toBeLessThan(50000);
		expect(priceDomain([50000])[1]).toBeGreaterThan(50000);
		expect(priceDomain([null, undefined])).toEqual([0, 10000]);
		expect(priceDomain([100])[0]).toBe(0); // never below zero
	});

	it('finds the nearest point', () => {
		expect(nearest([0, 10, 20], 14)).toBe(1);
		expect(nearest([0, 10, 20], 16)).toBe(2);
		expect(nearest([0, 10, 20], -5)).toBe(0);
		expect(nearest([], 3)).toBe(-1);
	});

	it('draws lines, areas and bars', () => {
		expect(linePath([0, 10], [5, null])).toBe('M0,5Z');
		expect(areaPath([0, 10], [5, 6], 20)).toBe('M0,5L10,6L10,20L0,20Z');
		expect(areaPath([], [], 20)).toBe('');
		expect(roundedTopBar(0, 0, 10, 0)).toBe('');
		expect(roundedTopBar(0, 10, 10, 20)).toMatch(/^M0,30V/);
	});
});
