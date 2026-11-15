// Per-device viewer preferences, kept in localStorage (which can be missing or
// blocked, so every access is guarded).

/** Colour mode. */
export type Theme = 'auto' | 'light' | 'dark';

function load<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
	try {
		const v = localStorage.getItem(key);
		return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
	} catch {
		return fallback;
	}
}

function save(key: string, value: string): void {
	try {
		localStorage.setItem(key, value);
	} catch {
		// private window or blocked storage: the choice just won't persist
	}
}

class Prefs {
	theme = $state<Theme>(load('fw.theme', ['auto', 'light', 'dark'] as const, 'auto'));
	/** The device's own preference, followed in Auto. */
	systemDark = $state(
		typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches
	);

	get dark(): boolean {
		return this.theme === 'dark' || (this.theme === 'auto' && this.systemDark);
	}

	setTheme(t: Theme) {
		this.theme = t;
		save('fw.theme', t);
	}
}

export const prefs = new Prefs();
