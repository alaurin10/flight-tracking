// One page's data: loading, loaded or failed. A newer load replaces an older one still
// in flight, so moving between trips never shows the previous trip's answer.
export class Resource<T> {
	data = $state<T | null>(null);
	error = $state<unknown>(null);
	#n = 0;

	get loading(): boolean {
		return this.data === null && this.error === null;
	}

	/** Start a load. `keep` leaves the current data showing until the new answer arrives. */
	load(p: Promise<T>, { keep = false } = {}): void {
		const n = ++this.#n;
		this.error = null;
		if (!keep) this.data = null;
		p.then(
			(d) => {
				if (n === this.#n) this.data = d;
			},
			(e: unknown) => {
				if (n === this.#n) this.error = e;
			}
		);
	}
}
