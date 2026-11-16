// Push alerts from the browser side: what this device can do, and (un)subscribing.
// Copied from Snow Watch; a device chooses kinds (price, deal, health) instead of places.

export type PushSupport =
	| 'ok'
	| 'unsupported' // no service worker / PushManager / Notification
	| 'ios-home-screen' // iPhone or iPad: works only from the Home Screen app
	| 'denied'; // the user blocked notifications for this site

export type Kind = 'price' | 'deal' | 'health';
export const KINDS: { id: Kind; label: string; hint: string }[] = [
	{
		id: 'price',
		label: 'Price alerts',
		hint: 'a trip or flexible date under its target or at a new low'
	},
	{ id: 'deal', label: 'Deals', hint: 'found deals and deal posts that match what you watch' },
	{
		id: 'health',
		label: 'Collector trouble',
		hint: 'when prices stop coming in, and when they resume'
	}
];

export function pushSupport(): PushSupport {
	if (typeof window === 'undefined') return 'unsupported';
	const ios = /iPad|iPhone|iPod/.test(navigator.userAgent);
	const standalone =
		window.matchMedia?.('(display-mode: standalone)').matches ||
		(navigator as unknown as { standalone?: boolean }).standalone === true;
	if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window))
		return ios && !standalone ? 'ios-home-screen' : 'unsupported';
	if (ios && !standalone) return 'ios-home-screen';
	if (Notification.permission === 'denied') return 'denied';
	return 'ok';
}

/** The server's VAPID key, or null when push isn't set up there (or you aren't signed in). */
export async function serverKey(): Promise<string | null> {
	const r = await fetch('/api/push/key', { credentials: 'same-origin' });
	if (!r.ok) return null;
	return ((await r.json()) as { key: string }).key;
}

function keyBytes(b64url: string): Uint8Array<ArrayBuffer> {
	const pad = '='.repeat((4 - (b64url.length % 4)) % 4);
	const raw = atob((b64url + pad).replace(/-/g, '+').replace(/_/g, '/'));
	const out = new Uint8Array(new ArrayBuffer(raw.length));
	for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
	return out;
}

export async function currentSubscription(): Promise<PushSubscription | null> {
	const reg = await navigator.serviceWorker.ready;
	return reg.pushManager.getSubscription();
}

type Result = { ok: boolean; detail?: string | null };

async function post(path: string, body: unknown): Promise<Result> {
	const r = await fetch(path, {
		method: 'POST',
		credentials: 'same-origin',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (!r.ok) {
		const detail = await r
			.json()
			.then((j: { detail?: string }) => j.detail)
			.catch(() => null);
		return { ok: false, detail: detail ?? `HTTP ${r.status}` };
	}
	return (await r.json()) as Result;
}

/** Ask permission (call from a tap), subscribe this device and save its kinds. */
export async function turnOn(key: string, kinds: Kind[]): Promise<Result> {
	const permission = await Notification.requestPermission();
	if (permission !== 'granted') return { ok: false, detail: 'Notifications were not allowed.' };
	const reg = await navigator.serviceWorker.ready;
	const sub =
		(await reg.pushManager.getSubscription()) ??
		(await reg.pushManager.subscribe({
			userVisibleOnly: true,
			applicationServerKey: keyBytes(key)
		}));
	return post('/api/push/subscribe', { subscription: sub.toJSON(), kinds });
}

export async function saveKinds(kinds: Kind[]): Promise<Result> {
	const sub = await currentSubscription();
	if (!sub) return { ok: false, detail: 'Alerts are off on this device.' };
	return post('/api/push/subscribe', { subscription: sub.toJSON(), kinds });
}

/** The kinds this device's subscription asked for, or null when it isn't on the server. */
export async function savedKinds(): Promise<Kind[] | null> {
	const sub = await currentSubscription();
	if (!sub) return null;
	const r = await fetch('/api/push/prefs', {
		method: 'POST',
		credentials: 'same-origin',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ endpoint: sub.endpoint })
	});
	return r.ok ? ((await r.json()) as { kinds: Kind[] }).kinds : null;
}

export async function sendTest(): Promise<Result> {
	const sub = await currentSubscription();
	if (!sub) return { ok: false, detail: 'Alerts are off on this device.' };
	return post('/api/push/test', { endpoint: sub.endpoint });
}

export async function turnOff(): Promise<Result> {
	const sub = await currentSubscription();
	if (!sub) return { ok: true };
	const res = await post('/api/push/unsubscribe', { endpoint: sub.endpoint });
	await sub.unsubscribe();
	return res;
}
