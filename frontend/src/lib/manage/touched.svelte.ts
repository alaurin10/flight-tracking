// When a form shows a field's error: once the field has been left, or after a save was
// tried. Typing into a fresh form doesn't shout at you.
import { tick } from 'svelte';
import type { Errors } from './validate';

export class Touched {
	tried = $state(false);
	#left = $state<Record<string, boolean>>({});

	touch(field: string): void {
		this.#left[field] = true;
	}

	/** The error to show for `field`, or '' while it should stay quiet. */
	show(field: string, errors: Errors): string {
		return (this.tried || this.#left[field]) && errors[field] ? errors[field] : '';
	}
}

/** After a refused save: move focus to the first field marked invalid. */
export async function focusFirstError(form: HTMLElement | null): Promise<void> {
	await tick();
	form?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
}
