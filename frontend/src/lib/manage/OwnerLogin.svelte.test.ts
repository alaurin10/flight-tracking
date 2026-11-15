// The owner password form: what it sends, and how it reports a wrong password.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/svelte';
import { tick } from 'svelte';
import OwnerLogin from './OwnerLogin.svelte';

const json = (status: number, body: unknown) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

async function settle() {
	for (let i = 0; i < 5; i++) {
		await Promise.resolve();
		await tick();
	}
}

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

describe('OwnerLogin', () => {
	it('posts the password and hands back the new state', async () => {
		const fetch = vi.fn(async () => json(200, { is_owner: true, owner_login_enabled: true }));
		vi.stubGlobal('fetch', fetch);
		const onSignedIn = vi.fn();
		const { container, getByLabelText } = render(OwnerLogin, { onSignedIn });
		await fireEvent.input(getByLabelText('Password'), { target: { value: 'hunter2hunter2' } });
		await fireEvent.submit(container.querySelector('form') as HTMLFormElement);
		await settle();
		expect(fetch).toHaveBeenCalledOnce();
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('/api/auth/login');
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body as string)).toEqual({ password: 'hunter2hunter2' });
		expect(onSignedIn).toHaveBeenCalledWith({ is_owner: true, owner_login_enabled: true });
	});

	it('says so when the password is wrong', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => json(401, { detail: 'wrong password' }))
		);
		const onSignedIn = vi.fn();
		const { container, getByLabelText, findByRole } = render(OwnerLogin, { onSignedIn });
		await fireEvent.input(getByLabelText('Password'), { target: { value: 'nope' } });
		await fireEvent.submit(container.querySelector('form') as HTMLFormElement);
		await settle();
		expect((await findByRole('alert')).textContent).toContain('isn’t right');
		expect(onSignedIn).not.toHaveBeenCalled();
	});

	it('shows the lockout message from the server', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => json(429, { detail: 'too many wrong passwords; try again in 1 min' }))
		);
		const { container, getByLabelText, findByRole } = render(OwnerLogin, {
			onSignedIn: () => {}
		});
		await fireEvent.input(getByLabelText('Password'), { target: { value: 'x' } });
		await fireEvent.submit(container.querySelector('form') as HTMLFormElement);
		await settle();
		expect((await findByRole('alert')).textContent).toContain('try again in 1 min');
	});
});
