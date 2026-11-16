// Which way the service worker answers a GET (src/service-worker.ts), kept here as a pure
// function so it can be tested. Copied from Snow Watch without its map tiles.

/** Owner endpoints: they depend on who is signed in, so never cached. */
export const PRIVATE = ['/api/auth/', '/api/admin/', '/api/push/'];

export type Route =
	/** Not answered by the service worker: straight to the network. */
	| 'network'
	/** The network first, the last good answer when offline. */
	| 'api'
	/** A page load: the network first, the cached app shell when offline. */
	| 'page'
	/** App files (hashed JS and CSS, icons): from the cache. */
	| 'shell';

export function route(
	url: URL,
	origin: string,
	opts: { navigate: boolean; shell: boolean }
): Route {
	if (url.origin !== origin) return 'network';
	const p = url.pathname;
	if (PRIVATE.some((x) => p.startsWith(x))) return 'network';
	if (p.startsWith('/api/')) return 'api';
	if (opts.navigate) return 'page';
	if (opts.shell) return 'shell';
	return 'network';
}

/** Only our own pages may be opened from a notification. */
export function openTarget(url: string | undefined | null, origin: string): string | null {
	try {
		const target = new URL(url || '/', origin);
		return target.origin === origin ? target.href : null;
	} catch {
		return null;
	}
}
