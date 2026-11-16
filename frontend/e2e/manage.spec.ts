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
	await expect(page.getByRole('button', { name: /A trip/ })).toHaveCount(0);
});

test('the four ways to watch are there once signed in', async ({ page }) => {
	await signIn(page);
	for (const name of [/A trip/, /Flexible dates/, /A region/, /A deal page/])
		await expect(page.getByRole('button', { name })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'What you’re watching' })).toBeVisible();
	await expect(page.getByTestId('manage-trip')).toContainText(['Tokyo in spring']);
});

test('add a trip, see its first price, pause it, delete it', async ({ page }, info) => {
	test.setTimeout(120_000);
	const problems = watch(page);
	const name = `E2E ${info.project.name} trip`;
	await signIn(page);

	await page.getByRole('button', { name: /A trip/ }).click();
	await expect(page.getByRole('heading', { name: 'Watch a trip' })).toBeVisible();
	await page.getByLabel('Where to').fill('Los Angeles');
	await page.getByRole('option', { name: /LAX/ }).click();
	await page.getByLabel('Leave').fill(ahead(40));
	await page.getByLabel('Come back').fill(ahead(44));
	await page.getByLabel('Alert me when it’s under').fill('300');
	await page.getByText('More options').click();
	await expect(page.getByText('SEA', { exact: true })).toBeVisible(); // from home
	await expect(page.getByLabel('Name', { exact: true })).toHaveAttribute(
		'placeholder',
		/^Los Angeles · /
	);
	await page.getByLabel('Name', { exact: true }).fill(name);
	expect(await sideways(page)).toBeLessThanOrEqual(1);
	await page.getByRole('button', { name: 'Start watching' }).click();

	// Back on the list: the save queued a fetch, and the worker prices it.
	const row = page.getByTestId('manage-trip').filter({ hasText: name });
	await expect(row).toContainText('SEA → LAX');
	await expect(row).toContainText('under $300');
	await expect(row.getByRole('status')).toContainText('Fresh prices are in', { timeout: 60_000 });
	await row.getByRole('link', { name: 'Open it' }).click();
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
	await expect(page.locator('main')).toContainText('$450'); // the fake source's cheapest fare

	await page.goto('/manage');
	await row.getByRole('button', { name: `More for ${name}` }).click();
	await page.getByRole('menuitem', { name: 'Pause' }).click();
	await expect(row.locator('.pill')).toHaveText('Paused');

	await row.getByRole('button', { name: `More for ${name}` }).click();
	await page.getByRole('menuitem', { name: 'Delete' }).click();
	const dialog = page.getByRole('alertdialog');
	await expect(dialog).toContainText(`Delete ${name}?`);
	await expect(dialog).toContainText('Prices already collected are kept');
	await dialog.getByRole('button', { name: 'Delete' }).click();
	await expect(row).toHaveCount(0);
	expect(problems).toEqual([]);
});

test('flexible dates: weekends in a month, and how many dates that is', async ({ page }, info) => {
	const name = `E2E ${info.project.name} weekends`;
	await signIn(page);
	await page.getByRole('button', { name: /Flexible dates/ }).click();
	await page.getByLabel('Where to').fill('Reno');
	await page.getByRole('option', { name: /RNO/ }).click();
	await expect(page.getByRole('button', { name: /Weekend/ }).first()).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(page.getByTestId('search-preview')).toContainText(/That’s \d+ dates? to price/);
	await page.getByText('More options').click();
	await page.getByLabel('Name', { exact: true }).fill(name);
	await page.getByRole('button', { name: 'Start watching' }).click();
	const row = page.getByTestId('manage-search').filter({ hasText: name });
	await expect(row).toContainText('SEA → RNO · weekends ·');
	await row.getByRole('button', { name: `More for ${name}` }).click();
	await page.getByRole('menuitem', { name: 'Delete' }).click();
	await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
	await expect(row).toHaveCount(0);
});

test('Track this on a found deal opens a new trip with its dates', async ({ page }) => {
	await signIn(page);
	await expect(page.getByRole('button', { name: /A trip/ })).toBeVisible();
	const depart = ahead(50);
	const back = ahead(57);
	await page.goto(`/manage?name=Lisbon&from=SEA&to=LIS&depart=${depart}&return=${back}`);
	await expect(page.getByRole('heading', { name: 'Watch a trip' })).toBeVisible();
	await expect(page.getByLabel('Leave')).toHaveValue(depart);
	await expect(page.getByLabel('Come back')).toHaveValue(back);
	await expect(page.getByText('LIS', { exact: true })).toBeVisible();
	await page.getByRole('button', { name: 'Cancel' }).click();
	await expect(page.getByRole('heading', { name: 'Watch a trip' })).toHaveCount(0);
});

test('a region in the summer, and a deal page', async ({ page }, info) => {
	const name = `E2E ${info.project.name} Europe`;
	await signIn(page);
	await page.getByRole('button', { name: /A region/ }).click();
	await expect(page.getByRole('heading', { name: 'Watch a region' })).toBeVisible();
	await page.getByRole('button', { name: 'Europe', exact: true }).click();
	await page.getByRole('button', { name: 'Summer' }).click();
	await page.getByLabel('Alert me when it’s under').fill('600');
	await page.getByText('More options').click();
	await expect(page.getByLabel('Leaving from (any of these words)')).toHaveValue('Seattle, SEA');
	await page.getByLabel('Name', { exact: true }).fill(name);
	await page.getByRole('button', { name: 'Start watching' }).click();
	const row = page.getByTestId('manage-watch').filter({ hasText: name });
	await expect(row).toContainText('Europe · Jun–Aug · under $600 · from Seattle, SEA');

	// A deal page. This server makes no outside requests (FLIGHTTRACK_SOURCE=fake), so the
	// address isn't fetched; it is saved as given.
	const url = `deals.example.com/${info.project.name}/`;
	await page.getByRole('button', { name: /A deal page/ }).click();
	await page.getByLabel('Page address').fill(url);
	await page.getByRole('button', { name: 'Start watching' }).click();
	const pageRow = page.getByTestId('manage-feed').filter({ hasText: url.replace(/\/$/, '') });
	await expect(pageRow.getByRole('status')).toContainText('New posts show on Deals');

	for (const [r, n] of [
		[row, name],
		[pageRow, url.replace(/\/$/, '')]
	] as const) {
		await r.getByRole('button', { name: `More for ${n}` }).click();
		await page.getByRole('menuitem', { name: 'Delete' }).click();
		await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
		await expect(r).toHaveCount(0);
	}
});

test('every form is usable on a phone without zooming', async ({ page }) => {
	await page.setViewportSize({ width: 380, height: 800 });
	await signIn(page);
	await expect(page.getByRole('heading', { name: 'Watch for' })).toBeVisible();
	const forms: [RegExp, string][] = [
		[/A trip/, 'Watch a trip'],
		[/Flexible dates/, 'Watch flexible dates'],
		[/A region/, 'Watch a region'],
		[/A deal page/, 'Watch a deal page']
	];
	for (const [button, heading] of forms) {
		await page.getByRole('button', { name: button }).click();
		await expect(page.getByRole('heading', { name: heading })).toBeVisible();
		await page.getByText('More options').click(); // check the folded fields too
		expect(await sideways(page), heading).toBeLessThanOrEqual(1);
		// Text at 16px or more (phones zoom into smaller inputs), controls at least 40px tall.
		const small = await page.locator('main form').evaluateAll((forms) =>
			forms.flatMap((f) =>
				[...f.querySelectorAll('input:not([type=checkbox]), select, textarea, button, summary')]
					.filter((el) => (el as HTMLElement).offsetParent !== null)
					.map((el) => {
						const s = getComputedStyle(el);
						const r = el.getBoundingClientRect();
						const tooSmallText =
							!['BUTTON', 'SUMMARY'].includes(el.tagName) && parseFloat(s.fontSize) < 16;
						return tooSmallText || r.height < 40 || r.right > window.innerWidth
							? `${el.tagName} ${el.id || el.textContent?.trim()}: ${s.fontSize} ${r.height}px`
							: null;
					})
					.filter(Boolean)
			)
		);
		expect(small, heading).toEqual([]);
		await page.goBack(); // shallow routing: Back closes the form
		await expect(page.getByRole('heading', { name: 'Watch for' })).toBeVisible();
	}
	expect(await sideways(page)).toBeLessThanOrEqual(1);
});

test('alert settings are saved', async ({ page }, info) => {
	// One project only: both would change the same stored settings at once.
	test.skip(info.project.name !== 'desktop', 'shared server state');
	await signIn(page);
	await page.getByText('Alert settings', { exact: true }).click();
	await expect(page.getByTestId('limits')).toContainText('of 30 trips');
	const days = page.getByLabel('Quiet days after an alert');
	await expect(days).toHaveValue(/\d+/);
	await days.fill('5');
	await page.getByRole('button', { name: 'Save alert settings' }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
	await page.reload();
	await page.getByText('Alert settings', { exact: true }).click();
	await expect(page.getByLabel('Quiet days after an alert')).toHaveValue('5');
	await page.getByLabel('Quiet days after an alert').fill('7');
	await page.getByRole('button', { name: 'Save alert settings' }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Saved' })).toBeVisible();
});
