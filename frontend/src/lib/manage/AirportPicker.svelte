<!-- An airport field: type a code, a city or an airport's name, then pick from the list
     (/api/airports). Chosen airports show as chips; `multiple` allows several (a flexible
     search's destinations). Keyboard: arrows move through the list, Enter picks, Escape
     closes it; a typed three-letter code can be picked with Enter even with no list. -->
<script lang="ts">
	import { searchAirports } from '../api';
	import type { AirportOut } from '../types.gen';

	interface Props {
		id: string;
		label: string;
		value: string[];
		multiple?: boolean;
		max?: number;
		error?: string;
		hint?: string;
		placeholder?: string;
	}
	let {
		id,
		label,
		value = $bindable(),
		multiple = false,
		max = 10,
		error,
		hint,
		placeholder = 'Code, city or airport'
	}: Props = $props();

	// What we know about each code shown, so chips can say the city.
	const known = $state<Record<string, AirportOut>>({});
	let text = $state('');
	let results = $state<AirportOut[]>([]);
	let open = $state(false);
	let active = $state(-1);
	let lookupError = $state<string | null>(null);
	let input = $state<HTMLInputElement | null>(null);
	let timer: ReturnType<typeof setTimeout> | undefined;
	let seq = 0;

	const listId = $derived(`${id}-list`);
	const showInput = $derived(multiple ? value.length < max : value.length === 0);

	// Stored codes (editing): look each up once so its chip shows the city.
	$effect(() => {
		for (const code of value) {
			if (known[code]) continue;
			searchAirports(code)
				.then((found) => {
					const hit = found.find((a) => a.iata === code);
					if (hit) known[code] = hit;
				})
				.catch(() => {});
		}
	});

	function onInput(q: string) {
		text = q;
		active = -1;
		lookupError = null;
		clearTimeout(timer);
		if (q.trim().length < 2) {
			results = [];
			open = false;
			return;
		}
		timer = setTimeout(async () => {
			const asked = ++seq;
			try {
				const found = await searchAirports(q.trim());
				if (asked !== seq) return; // a newer search is on its way
				results = found.filter((a) => !value.includes(a.iata));
				open = true;
				active = results.length ? 0 : -1;
			} catch {
				if (asked === seq) lookupError = 'Couldn’t search airports just now.';
			}
		}, 200);
	}

	function pick(code: string, airport?: AirportOut) {
		const c = code.toUpperCase();
		if (airport) known[c] = airport;
		if (!value.includes(c)) value = multiple ? [...value, c] : [c];
		text = '';
		results = [];
		open = false;
		active = -1;
		if (multiple) queueMicrotask(() => input?.focus());
	}

	function remove(code: string) {
		value = value.filter((c) => c !== code);
		queueMicrotask(() => input?.focus());
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' && results.length) {
			e.preventDefault();
			open = true;
			active = (active + 1) % results.length;
		} else if (e.key === 'ArrowUp' && results.length) {
			e.preventDefault();
			active = active <= 0 ? results.length - 1 : active - 1;
		} else if (e.key === 'Enter') {
			if (open && active >= 0 && results[active]) {
				e.preventDefault();
				pick(results[active].iata, results[active]);
			} else if (/^[a-zA-Z]{3}$/.test(text.trim())) {
				e.preventDefault();
				pick(text.trim());
			}
		} else if (e.key === 'Escape' && open) {
			e.preventDefault();
			e.stopPropagation();
			open = false;
		} else if (e.key === 'Backspace' && !text && multiple && value.length) {
			value = value.slice(0, -1);
		}
	}

	const place = (a: AirportOut | undefined) =>
		a ? (a.city && a.city !== a.name ? `${a.city} · ${a.name}` : a.name) : '';
</script>

<div class="field picker">
	<label for={id}>{label}</label>
	{#if value.length}
		<ul class="chips" aria-label={`${label}: chosen`}>
			{#each value as code (code)}
				<li class="chip">
					<strong>{code}</strong>
					{#if known[code]}<span class="city">{known[code].city || known[code].name}</span>{/if}
					<button
						type="button"
						class="x"
						aria-label={`Remove ${code}${known[code] ? ` (${known[code].city})` : ''}`}
						onclick={() => remove(code)}
					>
						<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"
							><path d="M6 6l12 12M18 6L6 18" /></svg
						>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
	{#if showInput}
		<div class="combo">
			<input
				{id}
				bind:this={input}
				type="text"
				role="combobox"
				autocomplete="off"
				autocapitalize="characters"
				spellcheck="false"
				aria-autocomplete="list"
				aria-expanded={open}
				aria-controls={listId}
				aria-activedescendant={open && active >= 0 ? `${id}-opt-${active}` : undefined}
				aria-invalid={error ? 'true' : undefined}
				aria-describedby={error ? `${id}-err` : hint ? `${id}-hint` : undefined}
				{placeholder}
				value={text}
				oninput={(e) => onInput(e.currentTarget.value)}
				onkeydown={onKey}
				onblur={() => setTimeout(() => (open = false), 150)}
			/>
			{#if open}
				<ul class="list" id={listId} role="listbox" aria-label={`${label}: matches`}>
					{#each results as a, i (a.iata)}
						<li
							id={`${id}-opt-${i}`}
							role="option"
							aria-selected={i === active}
							class:active={i === active}
							onmousedown={(e) => e.preventDefault()}
							onclick={() => pick(a.iata, a)}
							onkeydown={() => {}}
						>
							<strong>{a.iata}</strong>
							<span>{place(a)}</span>
							<span class="faint">{a.country}</span>
						</li>
					{:else}
						<li class="none" role="option" aria-selected="false" aria-disabled="true">
							No airport matches “{text.trim()}”.
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{/if}
	{#if error}<p class="err" id={`${id}-err`}>{error}</p>
	{:else if lookupError}<p class="err">{lookupError}</p>
	{:else if hint}<p class="hint" id={`${id}-hint`}>{hint}</p>{/if}
</div>

<style>
	.chips {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		max-width: 100%;
		min-height: 40px;
		padding: 0 0 0 12px;
		border-radius: 999px;
		background: var(--accent-soft);
		border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
		font-size: 0.9rem;
	}
	.city {
		color: var(--text-2);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		min-width: 0;
	}
	.x {
		flex: 0 0 40px;
		height: 40px;
		display: grid;
		place-items: center;
		border: 0;
		border-radius: 999px;
		background: transparent;
		color: inherit;
		cursor: pointer;
	}
	.x:hover {
		background: var(--hover);
	}
	.x svg {
		fill: none;
		stroke: currentColor;
		stroke-width: 2.2;
		stroke-linecap: round;
	}
	.combo {
		position: relative;
	}
	.list {
		position: absolute;
		z-index: 25;
		left: 0;
		right: 0;
		top: calc(100% + 4px);
		margin: 0;
		padding: 4px;
		list-style: none;
		max-height: 280px;
		overflow-y: auto;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 14px;
		box-shadow: var(--glass-shadow);
	}
	.list li {
		display: grid;
		grid-template-columns: 3.2em minmax(0, 1fr) auto;
		align-items: center;
		gap: 8px;
		min-height: 44px;
		padding: 4px 10px;
		border-radius: 10px;
		cursor: pointer;
		font-size: 0.92rem;
	}
	.list li span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.list li.active,
	.list li:hover {
		background: var(--accent-soft);
	}
	.list li.none {
		display: block;
		cursor: default;
		color: var(--text-2);
	}
</style>
