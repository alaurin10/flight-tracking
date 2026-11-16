// A new version of the app: the service worker has installed it and is waiting (copied
// from Snow Watch).
class AppUpdate {
	waiting = $state<ServiceWorker | null>(null);

	/** Watch for new versions (and check for one every hour while the app is open). */
	watch(): () => void {
		if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return () => {};
		let timer: ReturnType<typeof setInterval> | undefined;
		let reloading = false;
		// On a first visit the new worker takes control without replacing anything, so
		// only a change of controller on an already-controlled page means an update.
		let controlled = !!navigator.serviceWorker.controller;
		const onChange = () => {
			if (!controlled) {
				controlled = true;
				return;
			}
			if (reloading) return;
			reloading = true;
			location.reload();
		};
		navigator.serviceWorker.addEventListener('controllerchange', onChange);
		navigator.serviceWorker.getRegistration().then((reg) => {
			if (!reg) return;
			const track = (sw: ServiceWorker | null) => {
				if (!sw) return;
				const check = () => {
					// Only an update: on the very first visit there is nothing to replace.
					if (sw.state === 'installed' && navigator.serviceWorker.controller) this.waiting = sw;
				};
				check();
				sw.addEventListener('statechange', check);
			};
			track(reg.waiting);
			reg.addEventListener('updatefound', () => track(reg.installing));
			timer = setInterval(() => reg.update().catch(() => {}), 3600_000);
		});
		return () => {
			clearInterval(timer);
			navigator.serviceWorker.removeEventListener('controllerchange', onChange);
		};
	}

	/** Switch to the new version; the page reloads once it has taken over. */
	apply() {
		this.waiting?.postMessage({ type: 'skip-waiting' });
	}
}

export const appUpdate = new AppUpdate();
