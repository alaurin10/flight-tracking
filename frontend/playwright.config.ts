// Browser tests against the real API serving the built app, on a database seeded by
// `flighttrack demo` (no network). Two servers: the read-only pages on 8019, and the
// Manage flow on 8020 with a worker on the fake source (it adds and removes a trip, so
// it gets a database of its own).
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
	webServer: [
		{
			command: 'bash e2e/serve.sh 8019',
			url: 'http://localhost:8019/api/health',
			timeout: 120_000,
			reuseExistingServer: !process.env.CI
		},
		{
			command: 'bash e2e/serve.sh 8020 --worker',
			url: 'http://localhost:8020/api/health',
			timeout: 120_000,
			reuseExistingServer: !process.env.CI
		}
	]
});
