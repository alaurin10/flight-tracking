// The built app in a real browser against the API, on the demo database
// (e2e/serve.sh). Every project (phone, desktop) runs every test.
import { expect, test, type Page } from '@playwright/test';

const PAGES = [
	'/',
	'/trip/tokyo-spring',
	'/search/ski-weekends',
	'/deals',
	'/status',
	'/manage',
	'/more'
];

/** Page errors and CSP violations, collected while the test runs. */
function watch(page: Page): string[] {
	const problems: string[] = [];
	page.on('pageerror', (e) => problems.push(`error: ${e.message}`));
	page.on('console', (m) => {
		if (m.type() === 'error') problems.push(`console: ${m.text()}`);
	});
	return problems;
}

const sideways = (page: Page) =>
	page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

for (const path of PAGES) {
	test(`${path} loads without errors or sideways scrolling`, async ({ page }) => {
		const problems = watch(page);
		const res = await page.goto(path, { waitUntil: 'networkidle' });
		expect(res?.status()).toBe(200);
		await expect(page.locator('main')).not.toBeEmpty();
		await expect(page.locator('main')).not.toContainText('Loading…', { timeout: 10_000 });
		await expect(page.getByRole('alert')).toHaveCount(0);
		expect(await sideways(page)).toBeLessThanOrEqual(1);
		expect(problems).toEqual([]);
	});
}

test('the overview shows the seeded trip, the searches, alerts and deals', async ({ page }) => {
	await page.goto('/');
	const trip = page.getByTestId('trip');
	await expect(trip).toHaveCount(1);
	await expect(trip).toContainText('Tokyo in spring');
	await expect(trip).toContainText(/\$\d/);
	await expect(trip).toContainText(/BOOK|LEAN BOOK|HOLD|WAIT/);
	await expect(trip).toContainText('Target');
	await expect(trip.locator('svg[role="img"]')).toBeVisible(); // the small chart
	await expect(page.getByTestId('search')).toHaveCount(2);
	await expect(page.getByRole('heading', { name: 'Recent alerts' })).toBeVisible();
	await expect(page.getByText('Seattle to Paris').first()).toBeVisible();
	await expect(page.getByTestId('health-pill')).toHaveText(/Collecting normally/);
	await trip.click();
	await expect(page).toHaveURL(/\/trip\/tokyo-spring$/);
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('Tokyo in spring');
});

test('the trip page chart and its table agree', async ({ page }) => {
	await page.goto('/trip/tokyo-spring');
	const chart = page.locator('.pricechart').first();
	const line = chart.locator('path.line');
	await expect(line).toBeVisible();
	const n = Number(await line.getAttribute('data-n'));
	expect(n).toBeGreaterThan(10);
	await chart.locator('summary').click();
	const rows = chart.locator('tbody tr[data-kind="price"]');
	await expect(rows).toHaveCount(n);
	// The newest row, the chart's "now" and the price at the top are one number.
	const newest = (await rows.first().locator('td.num').textContent())?.trim();
	await expect(chart.locator('.readout')).toHaveText(`now ${newest}`);
	await expect(page.getByTestId('price-now')).toHaveText(newest!);
	// The target is drawn, and the keyboard reads the first check.
	await expect(chart.locator('line.target')).toHaveCount(1);
	await chart.locator('svg[role="application"]').focus();
	await page.keyboard.press('Home');
	const oldest = (await rows.last().locator('td.num').textContent())?.trim();
	await expect(chart.locator('.readout')).toContainText(oldest!);
	await expect(page.getByRole('link', { name: 'Open in Google Flights' })).toHaveAttribute(
		'href',
		/^https:\/\/www\.google\.com\/travel\/flights/
	);
	await expect(page.getByText('Latest offers')).toBeVisible();
});

test('the search grid opens a date and shows its history', async ({ page }) => {
	await page.goto('/search/ski-weekends');
	const cells = page.locator('button.cell');
	await expect(cells.first()).toBeVisible();
	expect(await cells.count()).toBeGreaterThan(10);
	await expect(page.locator('button.cell.best')).toHaveCount(1);
	const cheapest = page.locator('button.cell.best');
	await cheapest.click();
	const detail = page.getByTestId('date-detail');
	await expect(detail).toBeVisible();
	await expect(cheapest).toHaveAttribute('aria-pressed', 'true');
	await expect(detail.locator('path.line')).toBeVisible();
	await expect(detail.getByRole('link', { name: 'Open in Google Flights' })).toBeVisible();
	// The table lists the same dates, cheapest first.
	await page.getByText(/dates as a table, cheapest first/).click();
	const prices = await page
		.locator('.dest tbody td.price')
		.evaluateAll((tds) => tds.map((td) => Number(td.textContent!.replace(/[^\d]/g, ''))));
	expect(prices.length).toBe(await cells.count());
	expect(prices).toEqual([...prices].sort((a, b) => a - b));
	await detail.getByRole('button', { name: 'Close this date' }).click();
	await expect(detail).toHaveCount(0);
});

test('deals open on the Explore boards; posts are one tap away', async ({ page }) => {
	await page.goto('/deals');
	const board = page.getByTestId('board').first();
	await expect(board.getByRole('heading')).toContainText('Anywhere from SEA');
	const places = board.getByTestId('place');
	await expect(places.first()).toBeVisible();
	// Cheapest first, prices in dollars.
	const prices = await places.locator('.price').allInnerTexts();
	const cents = prices.map((p) => Number(p.replace(/[^0-9]/g, '')));
	expect(cents).toEqual([...cents].sort((a, b) => a - b));
	await expect(page.getByText('basic economy included')).toBeVisible();
	await page.getByRole('button', { name: 'Posts' }).click();
	await expect(page.getByTestId('deal').first()).toBeVisible();
	await expect(page.getByRole('button', { name: 'Matches only' })).toBeVisible();
});

test('the status page shows a healthy collector', async ({ page }) => {
	await page.goto('/status');
	await expect(page.getByTestId('health')).toContainText('Healthy');
	await expect(page.getByText('Requests per day')).toBeVisible();
	await expect(page.locator('.bar').first()).toBeVisible();
	await expect(page.getByRole('meter', { name: 'Request budget used' })).toBeVisible();
	await expect(page.getByTestId('pace')).toContainText('learned from how Google answers');
	await expect(page.getByTestId('lane').first()).toContainText('Detail pages');
	await expect(page.getByText('Server version')).toBeVisible();
});

test('a 380 px wide phone never scrolls sideways', async ({ page }) => {
	await page.setViewportSize({ width: 380, height: 800 });
	for (const path of PAGES) {
		await page.goto(path, { waitUntil: 'networkidle' });
		await expect(page.locator('main')).not.toContainText('Loading…');
		expect(await sideways(page), path).toBeLessThanOrEqual(1);
	}
	// Opening a date's history doesn't widen the page either.
	await page.goto('/search/tokyo-week');
	await page.locator('button.cell').first().click();
	await expect(page.getByTestId('date-detail')).toBeVisible();
	expect(await sideways(page)).toBeLessThanOrEqual(1);
});

test('the dark theme is chosen on More and kept on the next visit', async ({ page }) => {
	await page.goto('/more');
	await page.getByRole('button', { name: 'Dark' }).click();
	await expect(page.locator('html')).toHaveAttribute('data-mode', 'dark');
	await page.goto('/');
	await expect(page.locator('html')).toHaveAttribute('data-mode', 'dark');
	const bg = await page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);
	expect(bg).toBe('rgb(26, 35, 54)');
	await page.goto('/more');
	await page.getByRole('button', { name: 'Light' }).click();
	await expect(page.locator('html')).toHaveAttribute('data-mode', 'light');
});

test('the device’s dark setting is followed in Auto', async ({ browser }) => {
	const ctx = await browser.newContext({ colorScheme: 'dark' });
	const page = await ctx.newPage();
	await page.goto('http://localhost:8019/');
	await expect(page.locator('html')).toHaveAttribute('data-mode', 'dark');
	await ctx.close();
});

test('an unknown page, trip and search say so', async ({ page }) => {
	await page.goto('/no-such-page');
	await expect(page.getByRole('heading', { name: 'There’s nothing here' })).toBeVisible();
	await page.goto('/trip/nowhere');
	await expect(page.getByRole('heading', { name: 'No trip here' })).toBeVisible();
	await page.goto('/search/nowhere');
	await expect(page.getByRole('heading', { name: 'No search here' })).toBeVisible();
});

test('an unreachable API says so and offers a retry', async ({ page }) => {
	await page.route('**/api/**', (route) => route.abort('connectionrefused'));
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'Can’t reach Flight Watch' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
	await expect(page.getByRole('status').filter({ hasText: 'Can’t reach' })).toBeVisible();
	await page.unroute('**/api/**');
	await page.getByRole('button', { name: 'Try again' }).click();
	await expect(page.getByTestId('trip')).toHaveCount(1);
});

test('stale prices raise a notice linking to Status', async ({ page }) => {
	await page.route('**/api/health', (route) =>
		route.fulfill({
			status: 503,
			contentType: 'application/json',
			body: JSON.stringify({
				status: 'stale',
				newest_price_at: '2026-01-01T00:00:00Z',
				age_s: 50 * 3600,
				max_age_s: 36 * 3600,
				worker_heartbeat_age_s: null
			})
		})
	);
	await page.goto('/');
	const notice = page.getByRole('status').filter({ hasText: 'haven’t updated for 50 hours' });
	await expect(notice).toBeVisible();
	await notice.getByRole('link', { name: 'Status' }).click();
	await expect(page).toHaveURL(/\/status$/);
});

test('an empty database shows the empty state', async ({ page }) => {
	await page.route('**/api/overview', (route) =>
		route.fulfill({
			contentType: 'application/json',
			body: JSON.stringify({
				generated_at: '2026-11-15T00:00:00Z',
				home_airport: 'SEA',
				trips: [],
				searches: [],
				notifications: [],
				deals: [],
				health: { ok: false, problems: [], last_success_at: null, cooldown_until: null }
			})
		})
	);
	await page.goto('/');
	await expect(
		page.getByText('No prices yet. The first ones arrive within a few minutes of adding a trip.')
	).toBeVisible();
});
