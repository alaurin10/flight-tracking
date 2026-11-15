// jsdom has no ResizeObserver (Svelte's bind:clientWidth uses it). Charts in
// tests get a fixed width prop, so a no-op observer is enough.
class NoopResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver;
