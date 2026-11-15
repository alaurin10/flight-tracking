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
| `serve`: scheduler + report server in one process; Docker Compose; launchd; systemd | ✅ see `docs/running.md`; runs at start-up when nothing succeeded in 20 h |
| `demo`: synthetic history to see the UI today | ✅ |
| Rate-limit calibration | ⬜ not measured from a home address; a cloud sandbox was blocked every 4–6 requests. `flighttrack calibrate` on the always-on box |

The results page, the `primp` transport, the search filters, the calendar call and block
detection were checked against live Google on 2026-11-14; `docs/PLAN.md` has the table.
258 offline tests pass with and without the optional dependencies and with the clock moved
years ahead. **`docs/data-sources.md`** is the full analysis of every data source considered and
why the system is layered the way it is.

### Where it runs

On an always-on box at home with a residential IP: a mini PC, NAS or Raspberry Pi
running `docker compose up -d`, which starts one process that runs the daily job
at 03:15 and serves the report on port 8091 (LAN only: it has no sign-in yet). Cloud
IPs get captcha'd. **`docs/using.md`** is the day-to-day guide. The other files in `deploy/`
and `docs/running.md`, `docs/deploy-proxmox.md` predate the move to `backend/` (milestone 2 of
`docs/PLAN.md`) and are replaced by the deploy kit in milestone 5.
Storage is light: one SQLite file, ~100 MB/year unbounded or ~35 MB bounded with the default
weekly retention (`flighttrack compact`), which never touches the cheapest-offer price series.

```bash
uv run flighttrack demo && open ../data/out/demo.html   # the report with synthetic data, from backend/
```

### First run on the host

```bash
flighttrack doctor                # one real request: transport, page kind, parser, price units
flighttrack doctor --calendar     # one calendar RPC; set calendar.enabled only if prices look right
flighttrack run --dry-run         # the whole daily job with alerts printed, not sent
flighttrack calibrate --count 20 --interval 6 --yes   # measure the rate limit before raising the cap
```

`doctor` prints raw price values beside normalized cents. Check the dollar amounts against
google.com/travel/flights for the same search; if they match, price normalization is right.

---

## Install

### With Docker (the way it runs at home)

```bash
git clone https://github.com/alaurin10/flight-tracking.git
cd flight-tracking
cp .env.example .env                       # set TZ; the rest is for later milestones
GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
docker compose exec flighttrack flighttrack doctor --offline
docker compose run --rm -v "$PWD/my-trips.yaml:/tmp/t.yaml:ro" flighttrack \
    flighttrack import-config /tmp/t.yaml     # what to track; see Configuration
```

The page is at `http://<host>:8091/`. The database, the page and saved failure pages live in
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
uv run flighttrack run            # the daily job
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
| What is tracked: trips, flexible searches, deal watches and feeds | the database (`flighttrack import-config`; the app's Manage page from milestone 7) |

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
| `run` | The daily job: expand → sweep → fetch → deals → alert → health → html. Stages are isolated. |
| `serve` | `run` on a daily schedule **and** a web server for the report (`/`, `/status.json`, `POST /run`). One process for an always-on box. Runs once at start-up when nothing has succeeded in 20 hours. |
| `demo` | Seed synthetic history into `data/demo.db` and render `data/out/demo.html`. No network. |
| `compact` | Prune runner-up offers (>90d) and request logs (>180d), then VACUUM. Dry run unless `--yes`; `run` does it weekly. |
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
| `html` | Regenerate the static page. |

### The report

Every run rewrites `data/out/index.html` (and `flighttrack serve` serves it): trip cards with today's
price, a book-or-wait verdict and reasons, and the price history charted against your target;
the best date in each grid; every tracked departure as a calendar heatmap with the cheapest
outlined and a table twin; announced deals; and the collector's own health. Light and dark,
phone-first, hover readouts, no external requests, every date a deep link into the exact Google
Flights search. `flighttrack demo` renders it with synthetic data so you can see it before day one.

---

## How it stays reliable

The binding constraint is an undocumented per-IP limit at Google, and the worst failure mode is
silence — the system stops working and you conclude there are no deals. So:

- **Typed failures.** `NoResults` (move on), `BlockedError` (stop the run, cool off),
  `NetworkError` (retry once, then stop), `LayoutError` (try the next parser, save the page).
- **Source chain.** `google_html` (ours) → `fast_flights` (independent parser, optional) →
  `serpapi` (paid API on another IP, only with a key). A block stops the chain — every source
  shares your IP.
- **Cooldown is state.** A blocked run writes `cooldown_until`, escalating 1h → 4h → 12h → 24h;
  the next scheduled run skips itself. A success clears it.
- **Every request is logged** to `fetch_attempts` with outcome and latency — `status` shows the
  last week — and unreadable pages are saved to `data/failures/` (last 20).
- **Watchdog.** `health` notifies when nothing has succeeded in 36h, when all requests fail,
  when the page seems to have changed, or when a feed is dead. Once per problem set per day.
- **Pacing.** Serial, 4–8s jittered, capped per run, never retrying into a failure.

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

The shipped config appends every alert to `data/out/digest.md` (`notify.channel: file`). `smtp` sends
email instead (`SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `SMTP_TO`), and `none` turns delivery off.
The web app replaces all of these with browser push (`docs/PLAN.md`, M8).

Delivery failures are never recorded as sent, so a broken channel does not let the dedup window
swallow the retry, and any error inside a channel is reported, never raised into the run.

---

## Scheduling

**`docker compose up -d`** on a mini PC: `flighttrack serve` runs the job daily and serves the
report. Without Docker, one cron line does the same job:

```cron
15 3 * * *  cd /srv/flight-tracking/backend && .venv/bin/flighttrack run >> ../data/run.log 2>&1
```

Milestone 4 replaces the once-a-day run with a worker that collects in small batches.

`run` exits non-zero when the collector aborted or health is critical, so cron mail surfaces it —
and `health` sends a notification as well.

---

## Data model

```
trips, searches ──► expand ──► queries ──┬──► fetch / sweep ──► observations (append-only)
  (watch_queries links them)              │                          │
deal watches, feeds ──► deals ──► deal_posts  └──► alert ◄──────┤
                                            │                   │
                          fetch_attempts, run_log, state ──► health ──► notification
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
uv run pytest                 # 258 tests, all offline; also with `uv sync --frozen --extra fallback`
FLIGHTTRACK_BROWSER_TESTS=1 uv run pytest tests/test_page.py   # also drives the page's JS in Chromium (needs playwright)
docker build -f docker/Dockerfile .   # from the repository root
```

`FakeSource` and `FakeTransport` make every path testable without a request. The encoder test
against `fast-flights` skips if that extra is not installed. Logs from the long-running `serve`
are one JSON object per line; commands a person runs print plain text.

## Terms of service

Automated querying of Google Flights is not sanctioned, which is precisely why the free API
options closed. The volume here is trivial and personal, but the constraint is real and is the
reason pacing is conservative by design, and why the deal feeds — the one fully sanctioned input
— carry the "tell me when something is announced" question.
