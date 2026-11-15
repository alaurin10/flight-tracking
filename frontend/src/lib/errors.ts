// Turn a failed request into a sentence a person can act on (copied from Snow Watch).
import { ApiError } from './api';
import { instant } from './format';

export function friendlyError(e: unknown): string {
	if (e instanceof ApiError) {
		if (e.unreachable) return 'Couldn’t reach Flight Watch. Check your connection and try again.';
		if (e.status === 429) return 'Too many requests just now. Wait a minute and try again.';
		if (e.status >= 500) return 'The server had a problem. Try again in a moment.';
		return sentence(localTimes(e.message));
	}
	return e instanceof Error ? e.message : 'Something went wrong.';
}

/** The server's messages are lower-case fragments ("origin: XYZ is not …"); start them
 * with a capital and end them with a full stop. */
export function sentence(text: string): string {
	const t = text.trim();
	if (!t) return 'Something went wrong.';
	const s = t[0].toUpperCase() + t.slice(1);
	return /[.!?]$/.test(s) ? s : `${s}.`;
}

const ISO_INSTANT = /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})/g;

/** Times in a server message ("paused until 2027-01-10T15:00:00Z") in this browser's
 * time zone ("paused until Jan 10, 7:00 AM PST"). */
export function localTimes(text: string): string {
	return text.replace(ISO_INSTANT, (iso) => instant(iso));
}
