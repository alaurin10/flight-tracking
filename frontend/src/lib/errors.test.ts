import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import { friendlyError, localTimes, sentence } from './errors';

describe('friendlyError', () => {
	it('makes the server’s fragments into sentences', () => {
		expect(sentence('origin: XYZ is not an airport')).toBe('Origin: XYZ is not an airport.');
		expect(sentence('Done.')).toBe('Done.');
		expect(friendlyError(new ApiError(422, 'nights: must be at least 0'))).toBe(
			'Nights: must be at least 0.'
		);
	});

	it('says plainly when the server is away or busy', () => {
		expect(friendlyError(new ApiError(0, 'x'))).toMatch(/Couldn’t reach/);
		expect(friendlyError(new ApiError(429, 'x'))).toMatch(/Too many requests/);
		expect(friendlyError(new ApiError(500, 'x'))).toMatch(/server had a problem/);
	});

	it('shows times from the server in this browser’s time zone', () => {
		const out = localTimes('Collecting is paused until 2027-01-10T15:00:00Z (Google).');
		expect(out).not.toContain('2027-01-10T15');
		expect(out).toMatch(/^Collecting is paused until Jan 10, \d{1,2}:00 [AP]M .+ \(Google\)\.$/);
	});
});
