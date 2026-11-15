// The owner's Manage page in a real browser, against its own server (e2e/serve.sh 8020
// --worker): the API on a demo database plus a worker on the fake source, which prices
// a new trip with invented fares and sends nothing outside. Every project (phone,
// desktop) runs every test; each adds its own trip, named after the project.
import { expect, test, type Page } from '@playwright/test';

test.use({ baseURL: 'http://localhost:8020' });

const PASSWORD = 'e2e-owner-password';

function watch(page: Page): string[] {
	const problems: string[] = [];
	page.on('pageerror', (e) => problems.push(`error: ${e.message}`));
	page.on('console', (m) => {
		// The wrong-password test's 401 is expected; anything else is a problem.
		if (m.type() === 'error' && !m.text().includes('401')) problems.push(`console: ${m.text()}`);
	});
	return problems;
}

async function signIn(page: Page, password = PASSWORD) {
	await page.goto('/manage');
	await expect(page.getByRole('heading', { name: 'Owner sign-in' })).toBeVisible();
	await page.getByLabel('Password').fill(password);
	await page.getByRole('button', { name: 'Sign in' }).click();
}

/** YYYY-MM-DD, `days` from today. */
function ahead(days: number): string {
	const d = new Date();
	d.setDate(d.getDate() + days);
	return d.toISOString().slice(0, 10);
}

const sideways = (page: Page) =>
	page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test('Manage is in the navigation for everyone and asks for the password', async ({ page }) => {
	await page.goto('/');
	const nav = page.getByRole('navigation', { name: 'Main' }).filter({ visible: true });
	await nav.getByRole('link', { name: 'Manage' }).click();
	await expect(page).toHaveURL(/\/manage$/);
	await expect(page.getByRole('heading', { name: 'Owner sign-in' })).toBeVisible();
	await expect(page.getByTestId('manage-trip')).toHaveCount(0); // nothing shown before sign-in
});

test('a wrong password shows the error', async ({ page }) => {
	await signIn(page, 'not the password at all');
	await expect(page.getByRole('alert')).toHaveText('That password isn’t right.');
	await expect(page.getByLabel('Password')).toHaveAttribute('aria-invalid', 'true');
	await expect(page.getByRole('button', { name: 'Add trip' })).toHaveCount(0);
});

test('add a trip, see its first price, pause it, delete it', async ({ page }, info) => {
	test.setTimeout(120_000);
	const problems = watch(page);
	const name = `E2E ${info.project.name} trip`;
	await signIn(page);
	await expect(page.getByRole('heading', { name: 'Trips' })).toBeVisible();
	await expect(page.getByTestId('limits')).toContainText('of 20 trips');

	await page.getByRole('button', { name: 'Add trip' }).click();
	await expect(page.getByRole('heading', { name: 'Add a trip' })).toBeVisible();
	await page.getByLabel('Name').fill(name);
	await expect(page.getByText('SEA', { exact: true })).toBeVisible(); // From: the home airport
	const to = page.getByLabel('To', { exact: true });
	await to.fill('Los Angeles');
	await page.getByRole('option', { name: /LAX/ }).click();
	await page.getByLabel('Depart').fill(ahead(40));
	await page.getByLabel('Return').fill(ahead(44));
	await page.getByLabel('Target price').fill('300');
	expect(await sideways(page)).toBeLessThanOrEqual(1);
	await page.getByRole('button', { name: 'Add trip' }).click();

	// Back on the list: the save queued a fetch, and the worker prices it.
	const row = page.getByTestId('manage-trip').filter({ hasText: name });
	await expect(row).toContainText('SEA → LAX');
	await expect(row).toContainText('target $300');
	await expect(row.getByRole('status')).toContainText('has a fresh price', { timeout: 60_000 });
	await row.getByRole('link', { name: 'Open it' }).click();
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
	await expect(page.locator('main')).toContainText('$450'); // the fake source's cheapest fare

	await page.goto('/manage');
	await row.getByRole('button', { name: `Pause ${name}` }).click();
	await expect(row.locator('.pill')).toHaveText('Paused');
	await expect(row.getByRole('button', { name: `Resume ${name}` })).toBeVisible();

	await row.getByRole('button', { name: `Delete ${name}` }).click();
	const dialog = page.getByRole('alertdialog');
	await expect(dialog).toContainText(`Delete ${name}?`);
	await expect(dialog).toContainText('Prices already collected are kept');
	await dialog.getByRole('button', { name: 'Delete' }).click();
	await expect(row).toHaveCount(0);
	expect(problems).toEqual([]);
});

test('every form is usable on a phone without zooming', async ({ page }) => {
	await page.setViewportSize({ width: 380, height: 800 });
	await signIn(page);
	await expect(page.getByRole('heading', { name: 'Trips' })).toBeVisible();
	const forms: [string, string][] = [
		['Add trip', 'Add a trip'],
		['Add search', 'Add a flexible search'],
		['Add watch', 'Add a deal watch'],
		['Add feed', 'Add a deal feed']
	];
	for (const [button, heading] of forms) {
		await page.getByRole('button', { name: button }).click();
		await expect(page.getByRole('heading', { name: heading })).toBeVisible();
		expect(await sideways(page), heading).toBeLessThanOrEqual(1);
		// Text at 16px or more (phones zoom into smaller inputs), controls at least 40px tall.
		const small = await page.locator('main form').evaluateAll((forms) =>
			forms.flatMap((f) =>
				[...f.querySelectorAll('input:not([type=checkbox]), select, textarea, button')]
					.filter((el) => (el as HTMLElement).offsetParent !== null)
					.map((el) => {
						const s = getComputedStyle(el);
						const r = el.getBoundingClientRect();
						const tooSmallText = el.tagName !== 'BUTTON' && parseFloat(s.fontSize) < 16;
						return tooSmallText || r.height < 40 || r.right > window.innerWidth
							? `${el.tagName} ${el.id || el.textContent?.trim()}: ${s.fontSize} ${r.height}px`
							: null;
					})
					.filter(Boolean)
			)
		);
		expect(small, heading).toEqual([]);
		await page.goBack(); // shallow routing: Back closes the form
		await expect(page.getByRole('heading', { name: 'Trips' })).toBeVisible();
	}
	// The settings form sits on the page itself.
	await expect(page.getByLabel('Quiet days after an alert')).toBeVisible();
	expect(await sideways(page)).toBeLessThanOrEqual(1);
});

test('alert settings are saved', async ({ page }, info) => {
	// One project only: both would change the same stored settings at once.
	test.skip(info.project.name !== 'desktop', 'shared server state');
	await signIn(page);
	const days = page.getByLabel('Quiet days after an alert');
	await expect(days).toHaveValue(/\d+/);
	await days.fill('5');
	await page.getByRole('button', { name: 'Save alert settings' }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
	await page.reload();
	await expect(page.getByLabel('Quiet days after an alert')).toHaveValue('5');
	await page.getByLabel('Quiet days after an alert').fill('7');
	await page.getByRole('button', { name: 'Save alert settings' }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
});
