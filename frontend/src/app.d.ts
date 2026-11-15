// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	/** The app's version (frontend/package.json), set at build time. */
	const __APP_VERSION__: string;

	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		interface PageState {
			/** The Manage page's open form (shallow routing: Back closes it). */
			manage?: { form: 'trip' | 'search' | 'deal-watch' | 'feed'; id?: number };
		}
		// interface Platform {}
	}
}

export {};
