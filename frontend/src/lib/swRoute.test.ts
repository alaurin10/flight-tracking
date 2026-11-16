import { describe, expect, it } from 'vitest';
import { openTarget, route } from './swRoute';

const O = 'https://flights.example.com';
const at = (path: string) => new URL(path, O);

describe('the service worker routes', () => {
	it('keeps the last API answers for offline, never the owner’s', () => {
		const opts = { navigate: false, shell: false };
		expect(route(at('/api/overview'), O, opts)).toBe('api');
		expect(route(at('/api/finds'), O, opts)).toBe('api');
		for (const p of ['/api/auth/me', '/api/admin/trips', '/api/push/key'])
			expect(route(at(p), O, opts)).toBe('network');
	});

	it('loads pages from the network first and app files from the cache', () => {
		expect(route(at('/deals'), O, { navigate: true, shell: false })).toBe('page');
		expect(route(at('/_app/immutable/x.js'), O, { navigate: false, shell: true })).toBe('shell');
		expect(
			route(new URL('https://www.google.com/travel/flights'), O, { navigate: true, shell: false })
		).toBe('network');
	});

	it('opens only our own pages from a notification', () => {
		expect(openTarget('/trip/japan', O)).toBe(`${O}/trip/japan`);
		expect(openTarget(undefined, O)).toBe(`${O}/`);
		expect(openTarget('https://evil.example/x', O)).toBeNull();
	});
});
