// Whether this browser is signed in as the owner (copied from Snow Watch's Manage page,
// where the same state lived in the page). One copy for the whole app, so the Manage
// page, the More page and the forms agree, and a session that ends shows the sign-in again.
import { ApiError, getMe, logout } from './api';
import type { Me } from './types.gen';

class OwnerSession {
	/** null until /api/auth/me has answered. */
	me = $state<Me | null>(null);

	get isOwner(): boolean {
		return this.me?.is_owner ?? false;
	}

	async load(): Promise<Me> {
		const me = await getMe();
		this.me = me;
		return me;
	}

	signedIn(me: Me): void {
		this.me = me;
	}

	async signOut(): Promise<void> {
		this.me = await logout();
	}

	/** A 401 from an owner call: the session expired or was ended elsewhere. Shows the
	 * sign-in again and returns true; anything else returns false. */
	ended(e: unknown): boolean {
		if (e instanceof ApiError && e.status === 401 && this.me) {
			this.me = { ...this.me, is_owner: false };
			return true;
		}
		return false;
	}
}

export const owner = new OwnerSession();
