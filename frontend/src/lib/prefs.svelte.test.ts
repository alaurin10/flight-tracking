import { afterEach, describe, expect, it } from 'vitest';
import { prefs } from './prefs.svelte';

afterEach(() => {
	localStorage.clear();
	prefs.setTheme('auto');
});

describe('prefs', () => {
	it('remembers the theme and works out dark', () => {
		prefs.setTheme('dark');
		expect(localStorage.getItem('fw.theme')).toBe('dark');
		expect(prefs.dark).toBe(true);
		prefs.setTheme('light');
		expect(prefs.dark).toBe(false);
		prefs.setTheme('auto');
		prefs.systemDark = true;
		expect(prefs.dark).toBe(true);
		prefs.systemDark = false;
		expect(prefs.dark).toBe(false);
	});
});
