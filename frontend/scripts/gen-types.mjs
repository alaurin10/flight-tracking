// Generate src/lib/types.gen.ts from the backend's JSON Schema (src/lib/api-schema.json).
// Refresh the schema first with: cd backend && uv run flighttrack schema --out ../frontend/src/lib/api-schema.json
import { readFileSync, writeFileSync } from 'node:fs';
import { compile } from 'json-schema-to-typescript';

const schema = JSON.parse(readFileSync('src/lib/api-schema.json', 'utf8'));
const ts = await compile(schema, 'ApiTypes', {
	bannerComment:
		'// Generated from backend/flighttrack/api/models.py by `npm run types`. Do not edit.',
	unreachableDefinitions: true,
	additionalProperties: false,
	style: { useTabs: true, singleQuote: true, printWidth: 100 }
});
writeFileSync('src/lib/types.gen.ts', ts);
