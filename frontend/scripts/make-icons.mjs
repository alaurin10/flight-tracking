// Render the app icons from one SVG with the preinstalled Playwright Chromium.
//   node scripts/make-icons.mjs
// (Copied from Snow Watch; the art is the favicon's paper plane on the app's teal.)
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

// `pad` shrinks the art for maskable icons, whose outer 20% may be cropped by the phone.
const svg = (pad) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" fill="#0b7a73"/>
<g transform="translate(16 16) scale(${1 - pad}) translate(-16 -16)">
<path d="M6 18.5l20-8-6 10.5-3.5-4.5-4.5 2.5-2-2 4-2.5z" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round"/>
</g></svg>`;

const icons = [
	{ file: 'icon-192.png', size: 192, pad: 0.08 },
	{ file: 'icon-512.png', size: 512, pad: 0.08 },
	{ file: 'maskable-512.png', size: 512, pad: 0.3 },
	{ file: 'apple-touch-icon.png', size: 180, pad: 0.14 }
];

const browser = await chromium.launch();
for (const { file, size, pad } of icons) {
	const page = await browser.newPage({ viewport: { width: size, height: size } });
	await page.setContent(
		`<body style="margin:0">${svg(pad).replace('<svg ', `<svg width="${size}" height="${size}" `)}</body>`
	);
	await page.screenshot({ path: `static/icons/${file}`, omitBackground: false });
	await page.close();
}
await browser.close();
const { writeFileSync } = await import('node:fs');
writeFileSync('static/icons/icon.svg', svg(0.08));
console.log('icons written to static/icons/');
