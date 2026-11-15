// What the server says about itself (/api/meta), shared by every page: the currency
// prices are in, the version, whether owner sign-in is set up.
import { getMeta } from './api';
import type { MetaOut } from './types.gen';

class AppInfo {
	meta = $state<MetaOut | null>(null);

	get currency(): string {
		return this.meta?.currency ?? 'USD';
	}

	load(): void {
		getMeta()
			.then((m) => (this.meta = m))
			.catch(() => {});
	}
}

export const app = new AppInfo();
