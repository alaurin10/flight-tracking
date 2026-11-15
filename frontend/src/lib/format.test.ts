import { describe, expect, it } from 'vitest';
import {
	ago,
	alertReason,
	bytes,
	change,
	clock,
	daysBetween,
	duration,
	money,
	nights,
	outcomeLabel,
	stops,
	tripDates,
	until,
	weekday,
	weekdays
} from './format';

describe('format', () => {
	it('shows money in whole units from cents', () => {
		expect(money(103600)).toBe('$1,036');
		expect(money(17249)).toBe('$172');
		expect(money(0)).toBe('$0');
		expect(money(null)).toBe('–');
		expect(money(50000, 'EUR')).toBe('€500');
	});

	it('names dates without a time zone shift', () => {
		expect(weekday('2027-04-17')).toBe('Sat');
		expect(tripDates('2027-04-17', '2027-05-01')).toBe('Sat Apr 17 – Sat May 1');
		expect(tripDates('2027-04-17', null)).toBe('Sat Apr 17, one way');
		expect(daysBetween('2027-02-27', '2027-03-01')).toBe(2);
	});

	it('says how long ago and how long until', () => {
		const now = Date.parse('2026-11-15T12:00:00Z');
		expect(ago('2026-11-15T11:59:50Z', now)).toBe('just now');
		expect(ago('2026-11-15T11:30:00Z', now)).toBe('30 min ago');
		expect(ago('2026-11-15T00:00:00Z', now)).toBe('12 h ago');
		expect(ago('2026-11-10T12:00:00Z', now)).toBe('5 days ago');
		expect(ago(null, now)).toBe('never');
		expect(ago('2026-11-15T13:00:00Z', now)).toBe('just now');
		expect(until('2026-11-15T12:25:00Z', now)).toBe('in 25 min');
		expect(until('2026-11-15T11:00:00Z', now)).toBe('now');
	});

	it('reads flight times and durations', () => {
		expect(duration(640)).toBe('10 h 40 min');
		expect(duration(55)).toBe('55 min');
		expect(duration(120)).toBe('2 h');
		expect(stops(0)).toBe('Nonstop');
		expect(stops(1)).toBe('1 stop');
		expect(stops(2)).toBe('2 stops');
		expect(clock('2027-05-15 06:05')).toBe('6:05 AM');
		expect(clock('2027-05-15 00:30')).toBe('12:30 AM');
		expect(clock('2027-05-16 16:20', '2027-05-15 13:05')).toBe('4:20 PM +1');
		expect(clock(null)).toBe('–');
	});

	it('words the week’s change with a direction', () => {
		expect(change(-8.6)).toEqual({ text: '↓ 9%', dir: 'down' });
		expect(change(3.2)).toEqual({ text: '↑ 3%', dir: 'up' });
		expect(change(0.4)).toEqual({ text: '→ 0%', dir: 'flat' });
		expect(change(null).text).toBe('–');
	});

	it('words the rest', () => {
		expect(weekdays(['FRI'])).toBe('Fridays');
		expect(weekdays(['THU', 'FRI', 'SAT'])).toBe('Thursdays, Fridays and Saturdays');
		expect(nights(0)).toBe('one way');
		expect(nights(1)).toBe('1 night');
		expect(bytes(2179072)).toBe('2.1 MB');
		expect(alertReason('threshold')).toBe('under your target');
		expect(alertReason('new_kind')).toBe('new kind');
		expect(outcomeLabel('layout')).toBe('unreadable page');
	});
});
