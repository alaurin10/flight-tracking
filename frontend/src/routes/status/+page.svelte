<!-- Is the collector working? Problems in plain words, the last good price, the next
     batch, any cooldown, today's request budget, requests per day by outcome, the deal
     feeds, the canary, backups and the version. -->
<script lang="ts">
	import { getStatus } from '../../lib/api';
	import { ago, bytes, instant, until } from '../../lib/format';
	import { Resource } from '../../lib/resource.svelte';
	import type { StatusOut } from '../../lib/types.gen';
	import RequestsChart from '../../lib/charts/RequestsChart.svelte';
	import ErrorNote from '../../lib/components/ErrorNote.svelte';
	import Loading from '../../lib/components/Loading.svelte';

	const status = new Resource<StatusOut>();
	const load = (keep = false) => status.load(getStatus(), { keep });
	$effect(() => load());
	$effect(() => {
		const t = setInterval(() => load(true), 60_000);
		return () => clearInterval(t);
	});

	const future = (iso: string | null) => !!iso && Date.parse(iso) > Date.now();
	const appVersion = __APP_VERSION__;
</script>

<svelte:head><title>Status · Flight Watch</title></svelte:head>

<header class="pagehead">
	<h1>Status</h1>
	<p>How the price collector is doing.</p>
</header>

{#if status.data}
	{@const s = status.data}
	{@const crit = s.problems.filter((p) => p.severity === 'crit')}
	{@const warn = s.problems.filter((p) => p.severity === 'warn')}
	{@const used = Math.min(1, s.budget.limit ? s.budget.used / s.budget.limit : 0)}
	<section
		class="card verdict {crit.length ? 'crit' : warn.length ? 'warn' : 'ok'}"
		aria-labelledby="health-h"
		data-testid="health"
	>
		<h2 id="health-h">
			{#if crit.length}
				Needs attention
			{:else if warn.length}
				Working, with {warn.length} warning{warn.length === 1 ? '' : 's'}
			{:else}
				Healthy: collecting normally
			{/if}
		</h2>
		{#if s.problems.length}
			<ul class="problems">
				{#each s.problems as p (p.code)}
					<li class={p.severity}>
						<span class="pill {p.severity === 'crit' ? 'crit' : 'warn'}"
							>{p.severity === 'crit' ? 'problem' : 'warning'}</span
						>
						{p.message}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="muted">Nothing is wrong. Prices are being checked on schedule.</p>
		{/if}
		{#if s.info.length}
			<details class="info">
				<summary>Details</summary>
				<ul>
					{#each s.info as line, i (i)}<li>{line}</li>{/each}
				</ul>
			</details>
		{/if}
	</section>

	<div class="grid">
		<section class="card" aria-labelledby="coll-h">
			<h2 id="coll-h">Collecting</h2>
			<dl class="kv">
				<div>
					<dt>Last good price</dt>
					<dd title={instant(s.newest_price_at)}>{ago(s.newest_price_at)}</dd>
				</div>
				<div>
					<dt>Last successful batch</dt>
					<dd title={instant(s.last_success_at)}>{ago(s.last_success_at)}</dd>
				</div>
				<div>
					<dt>Next batch</dt>
					<dd>
						{#if s.next_batch_at}{until(s.next_batch_at)}
							<span class="faint">({instant(s.next_batch_at)})</span>{:else}not scheduled{/if}
					</dd>
				</div>
				<div>
					<dt>Cooldown</dt>
					<dd class:hot={future(s.cooldown_until)}>
						{#if future(s.cooldown_until)}
							until {instant(s.cooldown_until)} ({until(s.cooldown_until)})
						{:else}
							none
						{/if}
					</dd>
				</div>
				{#if s.blocked_streak}
					<div>
						<dt>Blocks in a row</dt>
						<dd class="hot">{s.blocked_streak}</dd>
					</div>
				{/if}
				<div>
					<dt>Calendar sweep</dt>
					<dd>{s.last_sweep_at ? ago(s.last_sweep_at) : 'not yet'}</dd>
				</div>
				<div>
					<dt>Date pairs tracked</dt>
					<dd class="num">{s.active_pairs}</dd>
				</div>
				<div>
					<dt>Checks queued</dt>
					<dd class="num">{s.jobs_queued}</dd>
				</div>
				<div>
					<dt>Worker</dt>
					<dd>
						{s.worker_heartbeat_age_s === null
							? 'no heartbeat file'
							: s.worker_heartbeat_age_s < 180
								? `alive (${s.worker_heartbeat_age_s} s ago)`
								: `silent for ${Math.round(s.worker_heartbeat_age_s / 60)} min`}
					</dd>
				</div>
			</dl>
		</section>

		<section class="card" aria-labelledby="budget-h">
			<h2 id="budget-h">Requests to Google</h2>
			<div class="budget">
				<div class="bnum">
					<span class="num big">{s.budget.used}</span>
					<span class="muted">of {s.budget.limit} in the last 24 hours</span>
				</div>
				<div
					class="meter"
					role="meter"
					aria-label="Request budget used"
					aria-valuemin="0"
					aria-valuemax={s.budget.limit}
					aria-valuenow={Math.min(s.budget.used, s.budget.limit)}
				>
					<i style:width="{used * 100}%" class:full={used >= 1}></i>
				</div>
				<p class="faint small">
					{s.budget.remaining
						? `${s.budget.remaining} left before collecting pauses`
						: 'Budget spent: collecting waits until older requests age out.'}
				</p>
			</div>
			<RequestsChart days={s.requests_by_day} budget={s.budget.limit} />
		</section>
	</div>

	<section class="card" aria-labelledby="runs-h">
		<h2 id="runs-h">Recent runs</h2>
		{#if s.recent_runs.length}
			<div class="table-wrap">
				<table class="data">
					<thead>
						<tr>
							<th scope="col">Started</th>
							<th scope="col" class="l">Kind</th>
							<th scope="col">Tried</th>
							<th scope="col">Ok</th>
							<th scope="col">Failed</th>
							<th scope="col" class="l">Notes</th>
						</tr>
					</thead>
					<tbody>
						{#each s.recent_runs as r (r.id)}
							<tr>
								<td>{instant(r.started_at)}</td>
								<td class="l">{r.kind}</td>
								<td class="num">{r.attempted}</td>
								<td class="num">{r.succeeded}</td>
								<td class="num" class:hot={r.failed > 0}>{r.failed}</td>
								<td class="l"
									>{[r.blocked ? 'blocked' : '', r.skipped ? 'skipped' : '', r.notes ?? '']
										.filter(Boolean)
										.join(' · ')}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="muted">No runs yet.</p>
		{/if}
	</section>

	<div class="grid">
		<section class="card" aria-labelledby="feeds-h">
			<h2 id="feeds-h">Deal feeds</h2>
			{#if s.feeds.length}
				<ul class="rows">
					{#each s.feeds as f (f.url)}
						<li class="feed">
							<div class="furl">{f.name ?? f.url.replace(/^https?:\/\//, '')}</div>
							<div class="faint small">
								{#if f.last_error}
									<span class="hot">failed {ago(f.last_at)}: {f.last_error}</span>
								{:else if f.last_ok_at}
									read {ago(f.last_ok_at)} · {f.last_items ?? 0} items
								{:else}
									not read yet
								{/if}
							</div>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="muted">No feeds.</p>
			{/if}
		</section>

		<section class="card" aria-labelledby="sys-h">
			<h2 id="sys-h">System</h2>
			<dl class="kv">
				<div>
					<dt>Canary (daily layout check)</dt>
					<dd>
						{#if s.canary?.at}
							<span class:hot={s.canary.result !== 'ok'}>{s.canary.result ?? '–'}</span>
							<span class="faint">{ago(s.canary.at)}</span>
						{:else}
							not run yet
						{/if}
					</dd>
				</div>
				<div>
					<dt>Last backup</dt>
					<dd>
						{#if s.backups.length}
							{ago(s.backups[0].created_at)}
							<span class="faint">({bytes(s.backups[0].bytes)}, {s.backups.length} kept)</span>
						{:else}
							none yet
						{/if}
					</dd>
				</div>
				<div>
					<dt>Database</dt>
					<dd>{bytes(s.database_bytes)} · {s.observations.toLocaleString('en-US')} prices</dd>
				</div>
				<div>
					<dt>Server version</dt>
					<dd>{s.version}{s.commit ? ` (${s.commit.slice(0, 7)})` : ''}</dd>
				</div>
				<div>
					<dt>App version</dt>
					<dd>{appVersion}</dd>
				</div>
				<div>
					<dt>Checked</dt>
					<dd>{instant(s.checked_at)}</dd>
				</div>
			</dl>
		</section>
	</div>
{:else if status.error}
	<ErrorNote error={status.error} retry={() => load()} />
{:else}
	<Loading />
{/if}

<style>
	.verdict h2 {
		font-size: 1.15rem;
	}
	.verdict.ok h2 {
		color: var(--good);
	}
	.verdict.crit {
		border-color: var(--warn-border);
	}
	.verdict.crit h2 {
		color: var(--warn);
	}
	.problems {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 8px;
	}
	.problems li {
		display: flex;
		align-items: baseline;
		gap: 8px;
	}
	.info {
		margin-top: 10px;
		font-size: 0.86rem;
		color: var(--text-2);
	}
	.info summary {
		cursor: pointer;
		min-height: 32px;
		display: flex;
		align-items: center;
	}
	.info ul {
		margin: 4px 0 0;
		padding-left: 1.2em;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr));
		gap: 14px;
		margin-top: 14px;
	}
	.grid + .card,
	.card + .grid {
		margin-top: 14px;
	}
	.hot {
		color: var(--warn);
	}
	.budget {
		margin-bottom: 14px;
	}
	.bnum {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 4px 8px;
	}
	.big {
		font-size: 1.9rem;
		font-weight: 700;
	}
	.meter {
		height: 10px;
		border-radius: 999px;
		background: var(--surface-2);
		overflow: hidden;
		margin: 8px 0 6px;
	}
	.meter i {
		display: block;
		height: 100%;
		border-radius: 999px;
		background: var(--accent);
	}
	.meter i.full {
		background: var(--o-other);
	}
	.small {
		font-size: 0.8rem;
		margin: 0;
	}
	.feed {
		min-width: 0;
	}
	.furl {
		overflow-wrap: anywhere;
		font-weight: 500;
	}
</style>
