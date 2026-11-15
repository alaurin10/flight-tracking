// Sets the colour mode on <html> before the app loads, so the page never flashes the
// wrong colours. The layout keeps it in step afterwards (see src/routes/+layout.svelte);
// the key matches src/lib/prefs.svelte.ts.
(function () {
	try {
		var mode = localStorage.getItem('fw.theme');
		var dark =
			mode === 'dark' ||
			(mode !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches);
		document.documentElement.setAttribute('data-mode', dark ? 'dark' : 'light');
	} catch {
		// Blocked storage: the light default stands until the app starts.
	}
})();
