# flighttrack

A self-hosted price sensor for the flights you actually care about. It answers three questions:

1. **My dates are set — when do I book?** A fixed trip is tracked daily to departure; you get an
   alert when it hits your target, an all-time low, or the cheapest slice of the route's history,
   and `advise` gives a book-or-wait verdict with its reasons.
2. **Any long weekend in a window — which is cheapest?** A pattern like "Fri→Mon, January
   through March, to SLC" becomes a grid of date pairs, each priced and compared with its own
   history. `report --sort price` is the answer.
3. **Tell me when a deal to Europe is announced.** Deal-site RSS feeds are polled and matched
   against your origin, region and price ceiling.

The unique value over any website is **history**: a single query tells you a price; a table of
observations over time tells you whether that price is good.

**Not** a booking system (it reports; you book in a browser). **Not** real-time — fares move
over days, so daily resolution is plenty.

---

## Status

> **Next:** this collector is becoming a web app at `flights.andrewlaurin.com`.
> [`docs/PLAN.md`](docs/PLAN.md) is the milestone plan, and it lists what a live check on
> 2026-11-14 found working and broken. Where it disagrees with the table below, the plan is
> newer.

| Piece | State |
|---|---|
| Own Google Flights client (no third-party scraper) | ✅ **verified live 2026-11-14**: 12 itineraries for SEA→SLC, every row and price equal to the rendered page; captured pages are regression tests |
| Fallback parser (`fast-flights`), paid API escape hatch (SerpApi) | ✅ optional, in the same chain |
| Calendar sweep (whole grid daily, ~12 calls) | ⚠️ off by default. Request and parser verified live 2026-11-14 and the bag filter is sent, but prices include basic economy (that filter slot is unknown), so they are compared only with each other |
| Typed failures, cooldown state, request log, failure artifacts | ✅ |
| Health watchdog with notification | ✅ also warns on empty feeds and parser mismatches |
| Fixed trips, windowed multi-weekday patterns, route filters | ✅ |
| Deal feeds (RSS) | ✅ The Flight Deal's main and Seattle feeds, checked 2026-11-14 |
| Book-or-wait advisor, price-sorted reports, `ingest` | ✅ |
| Report: trip cards with history charts and verdicts, best-of-grid, calendar heatmaps, deals, collector | ✅ light/dark, phone-first, no external requests |
| `worker`: collects in small batches under a rolling 24 h request budget, recovers from blocks and downtime by itself, daily canary, nightly backups | ✅ Docker Compose service with a heartbeat health check (M4) |
| Web API (FastAPI): public reads, owner sign-in, admin endpoints, rate limits, strict headers; the page at `/` until the app replaces it | ✅ Docker Compose `api` service on port 8091 (M5); [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| `demo`: synthetic history to see the app today | ✅ |
| Rate-limit calibration | ⬜ not measured from a home address; a cloud sandbox was blocked every 4–6 requests. `flighttrack calibrate` on the always-on box |

The results page, the `primp` transport, the search filters, the calendar call and block
detection were checked against live Google on 2026-11-14; `docs/PLAN.md` has the table.
368 offline tests pass with and without the optional dependencies and with the clock moved
years ahead. **`docs/data-sources.md`** is the full analysis of every data source considered and
why the system is layered the way it is.

### Where it runs

On an always-on box at home with a residential IP: a mini PC, NAS or Raspberry Pi
running `docker compose up -d`, which starts two containers from one image: the worker,
which collects on its own all day in small batches, and the API on port 8091, which serves
the site and `/api` (public and read-only, with one owner password that unlocks editing).
Cloud IPs get captcha'd. **[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)** is how to install
it, put HTTPS in front and keep it updated; **`docs/using.md`** is the day-to-day guide.
Storage is light: one SQLite file, ~100 MB/year unbounded or ~35 MB bounded with the default
weekly retention (`flighttrack compact`), which never touches the cheapest-offer price series.

```bash
uv run flighttrack demo && DB_PATH=../data/demo.db uv run uvicorn --factory flighttrack.api.app:create_app --port 8000   # from backend/
cd frontend && npm ci && npm run dev    # the app with synthetic data at http://localhost:5173
```

### First run on the host

```bash
flighttrack doctor                # one real request: transport, page kind, parser, price units
flighttrack doctor --calendar     # one calendar RPC; set calendar.enabled only if prices look right
flighttrack calibrate --count 20 --interval 6 --yes   # measure the rate limit before raising the budget
```

With Docker, prefix each with `docker compose run --rm worker`. `doctor` prints raw price values beside normalized cents. Check the dollar amounts against
google.com/travel/flights for the same search; if they match, price normalization is right.

---

## Install

### With Docker (the way it runs at home)

```bash
git clone https://github.com/alaurin10/flight-tracking.git
cd flight-tracking
cp .env.example .env                       # set TZ and OWNER_PASSWORD (12+ characters)
GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
docker compose exec worker flighttrack doctor --offline
docker compose run --rm -v "$PWD/my-trips.yaml:/tmp/t.yaml:ro" worker \
    flighttrack import-config /tmp/t.yaml     # what to track; see Configuration
docker compose ps                          # both should say (healthy) within a minute
```

The site is at `http://<host>:8091/` (put HTTPS in front before opening it to the internet:
`docs/DEPLOYMENT.md`). The database, nightly backups and saved failure pages live in
`./data` (owned by uid 1000; `sudo chown -R 1000:1000 data` if an older install made it as
root). The container's root file system is read-only. What is tracked lives in the database;
keep your own trips file out of the repository, which is public.

### Without Docker

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync --frozen                  # add --extra fallback for the fast-flights fallback parser
uv run flighttrack import-config ../config/seed.example.yaml   # or your own file
uv run flighttrack doctor         # one real request
uv run flighttrack worker         # collect on a schedule (Ctrl-C to stop)
uv run uvicorn --factory flighttrack.api.app:production_app --port 8091   # the site and /api
uv run flighttrack run            # or one full cycle now, for debugging
uv run flighttrack report --pattern denver-weekends --sort price
uv run flighttrack advise --pattern lisbon-autumn
```

Commands write under `data/` at the repository root wherever they are run from
(`DATA_DIR` in `.env` moves it).

### Where settings live

| Kind | Place |
|---|---|
| Secrets and per-install values | `.env`, never committed (`.env.example` lists them) |
| Tunables: search filters, pacing, alerts, health, retention, deal feeds | `config/flighttrack.yaml` |
| What is tracked: trips, flexible searches, deal watches and feeds | the database, edited on the app's Manage page (or with `flighttrack import-config`, or the owner endpoints under `/api/admin/`) |

---

## Configuration

What is tracked lives in the database:

| Kind | Answers | Shape |
|---|---|---|
| Trip | "My dates are fixed: when should I book?" | origin, destination, depart, return (or one way), target, max stops |
| Flexible search | "Any weekend in a window: which is cheapest?" | origin, one or more destinations, weekdays, nights, a window or the rolling horizon, target, priority |
| Deal watch | "Tell me when a deal to Europe is announced" | origin words, destination words or a region, maximum price |

Until the app can edit them (milestone 7), load them from a YAML file in the old config shape.
`config/seed.example.yaml` is an annotated example with invented trips:

```bash
flighttrack import-config my-trips.yaml     # creates what is missing, updates by name, never deletes
flighttrack export-config --out backup.yaml # the same shape, for backups
```

```yaml
home: SEA
routes:                      # each destination's label and its own target (cents)
  - {dest: LIS, label: Lisbon, target_price: 70000}
trips:                       # 1. fixed dates — tracked daily to departure
  - {name: lisbon-autumn, dest: LIS, depart: 2031-10-03, return: 2031-10-13, target_price: 65000}
patterns:                    # 2. become flexible searches
  - name: denver-weekends
    depart_dow: [THU, FRI]
    nights: 3
    routes: [DEN]            # several destinations make one search
    window: {start: 2031-01-01, end: 2031-03-31}
deals:                       # 3. announced deals (the default feeds are in config/flighttrack.yaml)
  watches:
    - {name: Europe from Seattle, origins: [Seattle, SEA, "West Coast", "US cities"],
       destinations: [europe], max_price: 60000}
```

Prices are in **cents**. A date pair's target is the lowest among the trips and searches that
want it, else its route's own. Limits: 20 active trips, 10 active searches, 400 date pairs in
all. Region names (`europe`, `japan`, `asia`, `hawaii`, `mexico`, `caribbean`,
`south america`, `oceania`, `africa`) expand to country and city words. Test a headline with
`flighttrack deals --test-match "Seattle to Paris for $412"`.

### Filters: "record everything, filter at report time"

`max_stops` is empty unless you set it, so nothing is discarded at fetch time. `carry_on_bags: 1`
and `exclude_basic_economy: true` stay on, not to discard options but so the recorded number
means one consistent thing; basic-economy fares mixed into the history corrupt every percentile
built on top.

### Coverage

The config shipped before milestone 3 expands to 154 date pairs against a 25/run cap (kept low until the home
address's limit has been measured with `calibrate`), so detail coverage rotates over several days
by priority (1 = daily, 2 = every 3 days, 3 = weekly), oldest-unfetched first, so nothing
starves. Fixed trips are priority 1. Enabling the calendar sweep refreshes the whole grid daily
and spends the detail budget on the dates that look interesting.

---

## Commands

| Command | Does |
|---|---|
| `worker` | The collector: one long-running process (see Scheduling). Logs one JSON object per line. |
| `healthcheck worker` | Exit 0 when the worker's heartbeat is under three minutes old (the container health check). |
| `run` | One full cycle now, for debugging: expand → sweep → fetch → deals → alert → health → compact. Stages are isolated. |
| `owner logout-all` | End every owner session (after changing `OWNER_PASSWORD`). |
| `schema` | Write the API's JSON Schema (`--out ../frontend/src/lib/api-schema.json`); CI fails when the committed copy is stale. |
| `demo` | Seed synthetic history into `data/demo.db` (`--db PATH` elsewhere). No network. |
| `compact` | Prune runner-up offers (>90d) and request logs (>180d), then VACUUM. Dry run unless `--yes`; the worker does it nightly. |
| `expand` | Materialise trips and patterns into `queries`. Idempotent, no network. |
| `fetch` | Fetch due queries through the source chain. `--dry-run` lists them. Skips itself during a cooldown. |
| `sweep` | Calendar RPC over every route/pattern window, then confirm the best candidates. Experimental. |
| `alert` | Evaluate fresh observations and notify. `--dry-run` prints. |
| `deals` | Poll feeds, match, notify. `--test-match TITLE` checks a headline offline. |
| `health` | Is the collector working? Exit 1 if not; `--notify` sends a notification (deduped daily). |
| `advise` | Book-or-wait verdicts with reasons, per date pair. |
| `report` | The grid. `--pattern`, `--dest`, `--label`, `--month`, `--sort price`, `--sparkline`, `--cheapest N`. |
| `status` | Run history, last week's request outcomes and latencies, health. **Look here first.** |
| `doctor` | Verify the data path. `--calendar`, `--dump PATH`, `--transport urllib`. |
| `calibrate` | Measure the rate limit (needs `--yes`). |
| `ingest` | Record prices from another tool, one JSON object per line. |

### The app

A SvelteKit single-page app in `frontend/`, built into the image and served by the API with the
SPA fallback. Anyone with the link can read it: the overview (trip cards with today's price, the
week's change, a book-or-wait verdict, target, all-time low and a small chart; the best date of
each flexible search; recent alerts and deals; a health pill), a page per trip (the price
history against the target, the verdict's reasons, the latest offers, alerts sent, the Google
Flights link), a page per search (one calendar heat map per destination, cheapest outlined, a
table cheapest first, and any date's history on a tap), deals, status and settings. Light and
dark, phone-first, charts usable by keyboard and each with a table twin, no third-party
requests. `flighttrack demo` seeds a database with synthetic data so it can be seen before day
one.

---

## How it stays reliable

The binding constraint is an undocumented per-IP limit at Google, and the worst failure mode is
silence — the system stops working and you conclude there are no deals. So:

- **Typed failures.** `NoResults` (move on), `BlockedError` (stop the run, cool off),
  `NetworkError` (retry once, then stop), `LayoutError` (try the next parser, save the page),
  `TransientError` (Google's own error 13 page: retry once in a few seconds, never stored as
  "no flights", no cooldown). A calendar call Google answers without data is `gated`: only the
  calendar lane pauses (30 min, then 2, 6, 24 h).
- **Source chain.** `google_html` (ours) → `fast_flights` (independent parser, optional) →
  `serpapi` (paid API on another IP, only with a key). A block stops the chain — every source
  shares your IP.
- **Cooldown is state.** A block writes `cooldown_until`, escalating 1h → 4h → 12h → 24h;
  the worker's next batch is the end of the cooldown plus 5–20 minutes. A success clears it.
- **A learned pace.** Every Google request counts against one rolling 24-hour allowance,
  counted from the request log, and the worker never goes over it in any 24-hour window. The
  allowance is learned (`pace.py`): it starts at 50, rises 50% after each clean day in which it
  was mostly used, up to `collect.pace.max_per_day` (720), and on a block falls to 60% of what
  was sent, holds three days and creeps back by 10% a day, below 85% of the volume that drew the
  block. Requests are spread evenly over the day, three at a time; each slot goes to the lane
  (detail pages or calendar windows) furthest under its share (`collect.shares`).
- **No run to miss.** What to fetch next is worked out from the database (trips first, then the
  calendar's picks, each search's cheapest dates, then the rest every 12 hours or so; calendar
  windows when they are six hours old), so a block, a
  restart or a week of downtime changes nothing: the worker looks at the same tables and goes on.
  Runs and jobs a crash left open are marked failed at start-up.
- **A daily canary.** One search on a busy nonstop route (`collect.canary_dest`) must come back
  as a full, self-consistent results page; if it does not, the page is saved and health turns
  critical (`layout_drift`) until a canary passes.
- **Every request is logged** to `fetch_attempts` with outcome and latency — `status` shows the
  last week — and unreadable pages are saved to `data/failures/` (last 20).
- **Watchdog.** `health` (hourly in the worker) notifies when nothing has succeeded in 36h, when
  all requests fail, when the page seems to have changed, or when a feed is dead; once per
  problem set per day, and once more when collecting recovers.
- **Pacing.** Serial, 4–8s jittered, five per batch with 25–45 minutes between batches, never
  retrying into a failure.
- **Backups.** Nightly `VACUUM INTO data/backups/flights-YYYYMMDD.db` plus a
  `tracked-YYYYMMDD.yaml` export of what is tracked; the newest 14 of each are kept.

`docs/data-sources.md` has the full reasoning, including what was wrong with relying on one
scraper and why the official APIs are not an option for a personal tracker.

---

## Alerting

Two-phase by design. A fixed threshold ages badly, but it is all that exists before history does.

- **Phase 1 (now):** at or below the target (the trip's own, else the route's), or a genuine
  all-time low for that date pair with ≥5 prior observations.
- **Phase 2 (`percentile_phase2_enabled: true` once ≥30 days of history):** at or below the 20th
  percentile of the route's trailing-60-day prices.
- **Deals:** every new feed post matching a watch, at most 5 per run, then a digest.

Phase 1 is expected to be **quiet** for the first weeks. That is the system accumulating.
Dedup: no re-alert on a date pair within 7 days unless it dropped a further 10%; at most 3
price alerts per run before a digest; only observations from the last 48h are considered.

### Notifications

Every alert (price, deal, found deal, health) is a record first: it is written to the
`notifications` table, which the overview shows, and only then pushed to the owner's devices
(browser push, set up with `flighttrack push keys`; each device chooses price, deal and health
alerts under More → Alerts). A tap opens the trip, search, Deals or Status page. Without VAPID
keys alerts are recorded and shown only. The app installs to a phone's Home Screen and keeps the
last answers for offline use.

A push that fails is reported in the log and never raised into the run; the alert stays
recorded, so the dedup window still holds and it is not sent twice. A device whose push
service says it is gone (404 or 410) is dropped, and so is one that fails five times in a row.

---

## Scheduling

**`docker compose up -d`** starts `flighttrack worker`, one process that collects on its own:

| Job | When | Does |
|---|---|---|
| slot | evenly through the day (3 requests about every 6 min at 720 a day) | detail fetches, then alerts; or the stalest calendar windows, whose best dates are flagged for the next detail fetches |
| pace | hourly | raise the learned pace after a clean, busy day |
| explore | in the slots, about 15% of them | one Explore call per region board and week ahead, each again when a day old: every place of a region priced at once (Deals → Explore) |
| deal finder | after each Explore slot; checks in the detail slots | fares well under a place's usual price (or under a watch's price, or a new low) are searched once with your filters; confirmed ones show on Deals → Found and alert once |
| deals | every 3 h | poll the feeds, record matches |
| health | hourly | notify when collecting stops, and when it recovers |
| canary | once a day | one known-busy search, checked for layout drift |
| job queue | every 15 s | "check now" requests from the API (`POST /api/admin/jobs`, and every saved trip or search) |
| backup, retention | nightly | the database copy and export; old runner-ups, logs, notifications |

All Google requests share one rolling 24-hour budget. Nothing depends on the clock: after a
block, a restart or downtime, the worker works out what is due from the database and carries
on. `docker compose ps` shows its health check (the heartbeat file it touches every 30 s).

`FLIGHTTRACK_SOURCE=fake docker compose up -d worker` runs it on invented prices with no
outside requests at all, which is what CI's smoke test does.

`flighttrack run` is still there for debugging: one full cycle, now. It exits non-zero when the
collector aborted or health is critical.

---

## Data model

```
trips, searches ──► expand ──► queries ──┬──► fetch / sweep ──► observations (append-only)
  (watch_queries links them)              │                          │
deal watches, feeds ──► deals ──► deal_posts  └──► alert ◄──────┤
                                            │                   │
                          fetch_attempts, run_log, state ──► health ──► notifications
                                                                │
                                                        report / advise / HTML
```

`observations` is append-only and the database enforces it (`BEFORE UPDATE`/`DELETE` triggers
raise). Every historical percentile depends on nothing ever rewriting that table. Calendar-sweep
prices are stored with `is_best = 0` and `source = 'google_calendar'`: context, never the series.
Expired queries and dropped routes are deactivated, never deleted. Schema changes are numbered,
additive migrations in `backend/flighttrack/store/migrations/`, applied whenever a command
opens the database; an older database is adopted without rewriting it. The one sanctioned
exception to append-only is `compact`, which removes runner-up offers and old request logs
(never `is_best` rows) inside one transaction that drops and restores the delete trigger.

---

## Development

```bash
cd backend
uv sync --frozen
uv run ruff check . && uv run ruff format --check .
uv run mypy flighttrack       # strict; modules from before M2 are listed as exceptions
uv run pytest                 # 365 tests, all offline; also with `uv sync --frozen --extra fallback`
uv run flighttrack schema --out ../frontend/src/lib/api-schema.json   # after changing an API model
docker build -f docker/Dockerfile .   # from the repository root
```

```bash
cd frontend
npm ci
npm run types                 # src/lib/types.gen.ts from the schema above
npm run lint && npm run check && npm test
npm run build && npm run e2e  # Playwright on the built app, against the API on a demo database
```

`FakeSource` and `FakeTransport` make every path testable without a request. The encoder test
against `fast-flights` skips if that extra is not installed. The API tests use FastAPI's
`TestClient` on a database seeded by `demo.seed`. Logs from the long-running `worker` and the
API are one JSON object per line; commands a person runs print plain text.

## Terms of service

Automated querying of Google Flights is not sanctioned, which is precisely why the free API
options closed. The volume here is trivial and personal, but the constraint is real and is the
reason pacing is conservative by design, and why the deal feeds — the one fully sanctioned input
— carry the "tell me when something is announced" question.
