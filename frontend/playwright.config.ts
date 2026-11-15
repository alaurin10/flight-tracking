// Browser tests against the real API serving the built app, on a database seeded by
// `flighttrack demo` (no network).
//   npm run build && npm run e2e
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: 'e2e',
	timeout: 30_000,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [['github'], ['list']] : 'list',
	use: { baseURL: 'http://localhost:8019', trace: 'off' },
	projects: [
		{ name: 'phone', use: { ...devices['Pixel 7'] } },
		{ name: 'desktop', use: { viewport: { width: 1280, height: 860 } } }
	],
	webServer: {
		command: 'bash e2e/serve.sh',
		url: 'http://localhost:8019/api/health',
		timeout: 120_000,
		reuseExistingServer: !process.env.CI
	}
});
