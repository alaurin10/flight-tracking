import { defineConfig } from 'vitest/config';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { readFileSync } from 'node:fs';

const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));

export default defineConfig({
	// The version shown on the More page; the server reports its own, so a stale app can say so.
	define: { __APP_VERSION__: JSON.stringify(version) },
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// SPA: the FastAPI app serves index.html for every non-/api route.
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	server: {
		proxy: { '/api': 'http://localhost:8000' }
	},
	test: {
		expect: { requireAssertions: true },
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.{test,spec}.{js,ts}'],
					exclude: ['src/**/*.svelte.{test,spec}.{js,ts}']
				}
			},
			{
				// Component tests: render charts in jsdom with the browser build of Svelte.
				extends: './vite.config.ts',
				resolve: { conditions: ['browser'] },
				test: {
					name: 'client',
					environment: 'jsdom',
					include: ['src/**/*.svelte.{test,spec}.{js,ts}'],
					setupFiles: ['src/lib/test-setup.ts']
				}
			}
		]
	}
});
