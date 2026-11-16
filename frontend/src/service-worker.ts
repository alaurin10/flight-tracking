// Offline support and push alerts (M8), copied from Snow Watch without its map tiles.
// - App files (hashed JS/CSS, icons) are cached at install and served from cache.
// - /api/* is network-first, falling back to the last good answer (up to 2 days old)
//   when offline. Owner endpoints (/api/auth, /api/admin, /api/push) are never cached.
// - Page loads go to the network first (so a new version shows up) and fall back to the
//   cached app shell only when the network is unreachable.
// - A new version waits until the page says to take over (its "Reload" button).
// - Push: the server sends {title, body, url, tag}; a tap opens that page of this app.
/// <reference lib="webworker" />
import { self } from '$app/service-worker';
import { assets, immutable } from '$app/manifest';
import { openTarget, route } from './lib/swRoute';

const shellFiles = [
	'/',
	'/index.html',
	...immutable.map((f) => `/${f.path}`),
	...assets.map((f) => `/${f.path}`)
];
const SHELL = `shell-${hash(shellFiles.join('|'))}`;
const API = 'api-v1';
const API_MAX_AGE_MS = 2 * 24 * 3600 * 1000;

function hash(s: string): string {
	let h = 0;
	for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
	return (h >>> 0).toString(36);
}

self.addEventListener('install', (event: ExtendableEvent) => {
	event.waitUntil(caches.open(SHELL).then((c) => c.addAll(shellFiles)));
});

self.addEventListener('message', (event: ExtendableMessageEvent) => {
	if ((event.data as { type?: string } | null)?.type === 'skip-waiting') void self.skipWaiting();
});

self.addEventListener('activate', (event: ExtendableEvent) => {
	event.waitUntil(
		caches
			.keys()
			.then((keys) =>
				Promise.all(keys.filter((k) => k !== SHELL && k !== API).map((k) => caches.delete(k)))
			)
			.then(() => self.clients.claim())
	);
});

/** Keep a copy of a good answer for offline use, without holding up the page. */
async function keep(request: Request, copy: Response) {
	const headers = new Headers(copy.headers);
	headers.set('x-sw-cached-at', String(Date.now()));
	const cache = await caches.open(API);
	await cache.put(request, new Response(await copy.blob(), { status: copy.status, headers }));
}

async function apiFirst(event: FetchEvent): Promise<Response> {
	const { request } = event;
	try {
		const res = await fetch(request);
		if (res.ok && res.type === 'basic') event.waitUntil(keep(request, res.clone()).catch(() => {}));
		return res;
	} catch {
		const cached = await caches.match(request, { cacheName: API });
		const at = Number(cached?.headers.get('x-sw-cached-at') ?? 0);
		if (cached && Date.now() - at < API_MAX_AGE_MS) return cached;
		return new Response(JSON.stringify({ detail: 'offline' }), {
			status: 503,
			headers: { 'content-type': 'application/json' }
		});
	}
}

async function pageNetworkFirst(request: Request): Promise<Response> {
	try {
		return await fetch(request);
	} catch {
		return (await caches.match('/index.html', { cacheName: SHELL })) ?? Response.error();
	}
}

self.addEventListener('fetch', (event: FetchEvent) => {
	const { request } = event;
	if (request.method !== 'GET') return;
	const url = new URL(request.url);
	const how = route(url, self.location.origin, {
		navigate: request.mode === 'navigate',
		shell: shellFiles.includes(url.pathname)
	});
	if (how === 'api') event.respondWith(apiFirst(event));
	else if (how === 'page') event.respondWith(pageNetworkFirst(request));
	else if (how === 'shell')
		event.respondWith(caches.match(request, { cacheName: SHELL }).then((r) => r ?? fetch(request)));
	// 'network': not answered here, so the browser goes straight to the network.
});

interface PushPayload {
	title?: string;
	body?: string;
	url?: string;
	tag?: string;
}

self.addEventListener('push', (event: PushEvent) => {
	let data: PushPayload;
	try {
		data = (event.data?.json() as PushPayload) ?? {};
	} catch {
		data = { title: event.data?.text() };
	}
	// `renotify` is in the spec but not in TypeScript's NotificationOptions.
	const options: NotificationOptions & { renotify?: boolean } = {
		body: data.body ?? '',
		tag: data.tag,
		renotify: !!data.tag,
		icon: '/icons/icon-192.png',
		badge: '/icons/icon-192.png',
		data: { url: data.url ?? '/' }
	};
	event.waitUntil(self.registration.showNotification(data.title ?? 'Flight Watch', options));
});

self.addEventListener('notificationclick', (event: NotificationEvent) => {
	event.notification.close();
	const target = openTarget(
		(event.notification.data as { url?: string } | null)?.url,
		self.location.origin
	);
	if (!target) return; // only open our own pages
	event.waitUntil(
		self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
			const open = wins.find((w) => new URL(w.url).origin === self.location.origin);
			if (open) return open.focus().then((w) => w.navigate(target));
			return self.clients.openWindow(target);
		})
	);
});
