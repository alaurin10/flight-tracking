# Flight Watch: implementation plan

This is the plan for turning `flighttrack` (a command-line price collector that writes one
static page) into a web app at `https://flights.andrewlaurin.com`, hosted on the home mini PC
the same way Snow Watch is. It is written to be handed to an implementing session one
milestone at a time.

"Flight Watch" is a working name. It appears in the page title and the web manifest only; the
Python package stays `flighttrack`.

## Status

Update this table in the same pull request that finishes a milestone.

| Step | What | Status | PR | Notes |
|---|---|---|---|---|
| M1 | Collector fixes | done | [#5](https://github.com/alaurin10/flight-tracking/pull/5) | 239 tests; ntfy removed instead of fixed; exclude-basic calendar slot still unknown; new problem 13 found in the live check |
| A | Owner: start collecting on the mini PC (optional) | not started | | |
| M2 | Restructure to the Snow Watch layout | in review | | 258 tests; root `Dockerfile` removed now, not in M5; watch file can still override tunables |
| M3 | Database as the source of truth | not started | | |
| M4 | The worker | not started | | |
| M5 | API and deploy kit | not started | | |
| B | Owner: go live at flights.andrewlaurin.com | not started | | |
| M6 | Frontend: read-only app | not started | | |
| M7 | Manage | not started | | |
| M8 | Push alerts and installable app | not started | | |
| C | Owner: turn on alerts | not started | | |
| M9 | Hardening and 1.0 | not started | | |

Decisions log (add a line when a milestone departs from this document, with the reason):

- 2026-11-14: Site is public and read-only; one owner password unlocks editing. Stack copies
  Snow Watch. Alerts go by browser push only.
- 2026-11-14 (M1, owner's request): ntfy is dropped from the plan. M1 deletes the ntfy channel
  instead of fixing its `Title` header; `file` is the shipped channel and `smtp` stays until M8.
  A config that still says `channel: ntfy` is refused with a message saying so.
- 2026-11-14 (M1, owner's request): no Healthchecks.io pings and no Uptime Kuma monitor. Rule 1
  under Reliability rules now has two signals. Trade-off: if the mini PC, the VM or the
  container dies outright, nothing outside it notices; the owner finds out from `/status` or
  from the absence of alerts.
- 2026-11-14 (M1): `USER_AGENT` is Chrome 145, the same major version `primp` impersonates
  (`chrome_145`), so the user agent and the TLS fingerprint agree. Bump both together.
- 2026-11-14 (M1): `default_transport("auto")` stays silent when `primp` is simply not
  installed (the core install, CI's `core` job); any other failure prints the warning line.
- 2026-11-14 (M1): the shipped `fetch.max_queries_per_run` is 25; the code default stays 60.
- 2026-11-14 (M1): the parser-mismatch count that `health` reads is stored in each
  observation's `raw.page_min`, so no schema change was needed.
- 2026-11-15 (M1): the exclude-basic calendar slot was not searched for. Method 1 needs a
  normal browser; method 2 needs requests outside `doctor` and more than a session's budget.
- 2026-11-15 (M2): the root `Dockerfile` is deleted in M2 instead of M5. It built from `src/`,
  which no longer exists; `docker/Dockerfile` replaces it.
- 2026-11-15 (M2): the watch file (`--config`) may still carry any tunable section, which then
  overrides `config/flighttrack.yaml` key by key, so a checkpoint-A `config.yaml` keeps working
  unchanged. An `output:` section is ignored with a warning (paths come from `Settings`). A
  missing `config/flighttrack.yaml` means the code defaults.
- 2026-11-15 (M2): the plan did not place `notify`, `deals.enabled`, `deals.max_alerts_per_run`
  or `deals.lookback_days`. `notify` and the last two are tunables; `deals.enabled` stays in the
  watch file with `deals.watches`, because enabling deals without watches is a config error. A
  relative `notify.file.path` is under `DATA_DIR`.
- 2026-11-15 (M2): `flighttrack/db.py` and `schema.sql` are gone. `utcnow`, `parse_ts`,
  `get_state` and `set_state` are imported from `flighttrack.store.db`. Commands open the
  database with `open_db()` (`connect` plus `migrate`). `migrate()` also re-creates an
  append-only trigger if one is missing, which the old connect did by re-applying `schema.sql`,
  and `compact` now drops and re-creates the delete trigger inside the same transaction as the
  delete.
- 2026-11-15 (M2): `html.py` is exempt from ruff's line-length rule: most of it is embedded CSS
  and JS, and M6 deletes it.
- 2026-11-15 (M2): plain-text log lines for commands a person runs are the bare message (Snow
  Watch prefixes level and logger), so `fetch` and `sweep` read as before. `serve` logs JSON.
  `health.run` logs its problems; `flighttrack health` prints the report itself.
- 2026-11-15 (M2): the non-Docker files in `deploy/` and `docs/running.md`,
  `docs/deploy-proxmox.md` install from the repository root with the old extras and Python 3.10,
  so they no longer work. They are left as they are for M5 to delete, as planned.
- 2026-11-15 (M2): the compose service is still called `flighttrack` (checkpoint A's
  `docker compose exec flighttrack …` commands keep working). Compose mounts `./config.yaml`
  read-only over the copy in the image until M3 removes it.

## How to work from this document

To start a milestone, give a fresh session this prompt:

> Read `docs/PLAN.md` and `CLAUDE.md`. Implement milestone M*n* as specified. Work on a new
> branch, keep the tests offline, open a pull request, and update the status table.

Rules for every milestone:

1. **Do one milestone per pull request.** Do not start the next one. Things listed under
   "Not in this milestone" belong to a later one even when they look easy.
2. **Read the "Read first" files before writing code.** Where this document and the code
   disagree about what exists, the code is right; note the difference in the decisions log.
3. **The acceptance list is the definition of done.** Run every command in it and paste the
   results into the pull request.
4. **Tests never touch the network.** Use `FakeSource`, `FakeTransport` (`tests/helpers.py`)
   and the saved fixtures.
5. **Live requests to Google are rationed.** See [Live requests](#live-requests). A cloud
   session's IP address is shared and gets blocked quickly; that is expected and is not a bug
   in the code.
6. **Never commit** `data/`, `out/`, `.env`, a database, or real trip dates.
7. **Keep the database.** `data/flights.db` holds price history that cannot be collected
   again. Migrations only add. `observations` stays append-only.
8. Snow Watch is the reference for anything not spelled out here:
   `git clone --depth 1 https://github.com/alaurin10/Snow-Forecast` (public, MIT, same owner).
   The [reference map](#snow-watch-reference-map) says which file to copy for what.

## Where things stand (2026-11-14)

### What exists

`src/flighttrack` is about 6,000 lines with one dependency (PyYAML) and two optional ones
(`primp`, `fast-flights`). The daily job is `jobs.run_daily`: expand → sweep → fetch → deals →
alert → health → compact → html.

| Module | Role |
|---|---|
| `gflights/tfs.py` | Builds the `tfs=` search parameter (hand-written protobuf) |
| `gflights/transport.py` | HTTP through `primp` (browser TLS fingerprint) or `urllib` |
| `gflights/page.py` | Classifies a response: results, no results, consent, blocked, error, unknown layout |
| `gflights/results.py` | Reads itineraries out of the page's data block |
| `gflights/calendar.py` | The price-graph call: one request prices about two months of dates |
| `source.py` | `Offer`, typed errors, the source chain (`google_html` → `fast_flights` → `serpapi`) |
| `fetch.py` | Picks due queries, paces requests, stores observations, cooldown state |
| `sweep.py` | Calendar sweep, then confirming fetches |
| `expand.py` | Turns `config.yaml` trips and patterns into rows in `queries` |
| `alert.py`, `deals.py`, `health.py`, `notify.py` | Price alerts, deal feeds, watchdog, delivery |
| `report.py`, `html.py` | Grids, book-or-wait verdicts (`advise`), the static page |
| `serve.py` | A once-a-day scheduler plus a stdlib web server |
| `compact.py`, `db.py`, `schema.sql`, `config.py`, `cli.py`, `demo.py` | Retention, storage, config, commands, synthetic data |

### Verified against live Google on 2026-11-14

Earlier sessions could not reach Google. This one could, so these are facts, not hopes.

| Check | Result |
|---|---|
| Results page, SEA→SLC, Jan 15–18 2027, shipped filters | 12 itineraries, cheapest $394. All 12 rows and prices equal the page rendered in Chromium |
| Same fetch through `primp` 2.0.1 (`chrome_145`) | Works. SEA→HND Apr 10–24 2027: 9 itineraries, $1,089 nonstop |
| Search filters in `tfs` | Honored. No filters $304; carry-on only $347; exclude basic $394; both $394. The page shows "1 carry-on bag" and "Economy (exclude Basic)" |
| Calendar call | HTTP 200, 31 dated prices for a 31-day window |
| Block detection | Google answered HTTP 429 with a redirect to `/sorry/index` on roughly every fourth to sixth request from the sandbox. `classify()` returned `blocked` each time |
| `expand` on the shipped config | 154 queries (1 trip, 128 Tokyo dates, 25 Salt Lake City dates) |
| Test suite | 194 pass, 3 fail, 1 skipped on CI's clock. The three were date-dependent and are fixed in the pull request that added this plan (197 pass) |

The captured responses are in `tests/fixtures/live-20261114/` with their expected values.

### What is broken or missing

| # | Problem | Evidence | Fixed in |
|---|---|---|---|
| 1 | **Notifications crash the stage.** Every subject starts with ✈ or ⚠; the old ntfy channel put it in an HTTP header, which raised `UnicodeEncodeError`. The callers catch only `NotifyError`, so price alerts, deal alerts and the "tracker is broken" warning all failed | Reproduced against a local server | M1 (channel removed; any channel error becomes `NotifyError`) |
| 2 | **Two of three deal feeds are useless.** `secretflying.com/feed/` now redirects to the HTML home page. `fly4free.com/feed/` lists Europe-origin fares in € and £, which never match a Seattle watch. Only `theflightdeal.com/feed/` works | Fetched all three with `deals.parse_feed` | M1 |
| 3 | **The calendar sweep ignores the bag and fare filters**, so its prices are not comparable with the tracked series ($304 against $394 for the same dates). `sweep.promising()` compares the two and would flag false bargains every day | Calendar $304 equals the unfiltered results page | M1 |
| 4 | **The request limit is close.** A handful of requests a few seconds apart drew a block in the sandbox. The home address will tolerate more, but nobody has measured it | See table above | M1, M4, B |
| 5 | **Tests depend on today's date.** Three failed on `main` and are fixed (two in `tests/test_deals.py`, one in `tests/test_sweep.py`). Two more pass today and will fail later: `tests/test_scenarios.py::test_window_reactivates_a_previously_expired_row` within about six weeks, and `tests/test_page.py::test_page_has_every_section_and_trip_target` once its 2027 trip date has passed | Suite run with the clock moved 7 to 800 days ahead | M1 |
| 6 | **The scheduler loses days.** One burst at 03:15. A block ends the run and nothing resumes when the cooldown ends. A restart during the run skips the day. Nothing catches up after downtime | `serve.Scheduler` | M1 (start-up catch-up), M4 |
| 7 | Trip time for connecting flights leaves out the layover (267 min recorded, 532 real) | `Itinerary.duration_min` sums segments; the total is at `raw[0][9]` | M1 |
| 8 | The "best" and "other" labels are swapped. `payload[2]` is the page's "Top departing flights" and `payload[3]` is "Other departing flights"; `results.itineraries` labels them the other way round. Prices are unaffected | Fixture against the rendered page | M1 |
| 9 | The tolerant parser can hide a layout change. A moved index turns a field into `None` without any error | By design of `results._at` | M1 (self-check), M4 (canary) |
| 10 | The web server cannot face the internet: stdlib `http.server`, no sign-in, and `POST /run` open to anyone | `serve.py` | M5 |
| 11 | Real trip dates are committed to a public repository in `config.yaml` | Repository visibility | M3 |
| 12 | Two processes cannot share the database safely (no `busy_timeout`), and there are no backups | `db.connect` | M2, M4 |
| 13 | **A Google-side error is recorded as "no itineraries".** On 2026-11-15 the results page for SEA→SLC Jan 13–16 came back with `ds:1` = `[13, null, [ErrorResponse …]]` and `errorHasStatus`. `classify()` calls any `errorHasStatus` page `no_results`, so `fetch.run` marks the query fetched and resets the failure counter. It was neither empty nor a block: the calendar priced that date at $277 and the next request succeeded | `tests/fixtures/live-20261115/` | not assigned; M4 (with the canary) is the natural place. Needs a real "no flights" page to tell the two apart |

## Target

### Shape

```
browser ─► Cloudflare Tunnel (HTTPS) ─► api :8000   FastAPI: the SvelteKit app and /api
           (the cloudflared app in        │ reads; writes only trips, searches, deal
            the Home Assistant VM)        │ watches, jobs, owner sessions, push subscriptions
                                        SQLite (WAL)  ./data/flights.db
                                          ▲ writes
   Google Flights, deal feeds  ◄───────── worker   APScheduler: fetch batches, calendar sweep,
                                                   deals, alerts, canary, job queue, backups
```

Both containers run from one image on the Docker VM, from `/opt/flight-tracking`, with the API
on host port **8091** (8080 is the Home Hub dashboard, 8090 is Snow Watch). Outbound requests
leave from the home address, which is what keeps Google from treating them as a data centre.

### Repository layout (from M2)

```
backend/
  pyproject.toml  uv.lock  .python-version
  flighttrack/
    gflights/        unchanged role
    store/           db.py  migrations/NNN_*.sql  backup.py  retention.py
    api/             app.py  auth.py  public.py  admin.py  models.py  ratelimit.py  push_api.py
    watches.py       trips, searches, deal watches: validation and CRUD (M3)
    scheduler.py     the worker (M4)
    settings.py  config.py  logs.py  cli.py  source.py  fetch.py  sweep.py  expand.py
    alert.py  deals.py  health.py  notifier.py  push.py  report.py  jobs.py  canary.py
    data/airports.csv
  tests/             fixtures/live-20261114/ …
frontend/            SvelteKit (static adapter), from M6
config/flighttrack.yaml      tunables only, nothing personal
config/seed.example.yaml     an example trip list
docker/Dockerfile  docker-compose.yml  .env.example
deploy/deploy.sh  deploy/flightwatch-deploy.service  deploy/flightwatch-deploy.timer
docs/PLAN.md  ARCHITECTURE.md  DEPLOYMENT.md  deploy-notes-home.md  SOURCES.md
CLAUDE.md
```

### Where each kind of setting lives

| Kind | Place | Examples |
|---|---|---|
| Secrets and per-install values | `.env` (never committed) | `OWNER_PASSWORD`, `PUBLIC_URL`, `TRUSTED_PROXY`, `TZ`, `HOME_AIRPORT`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `CONTACT_EMAIL`, `SERPAPI_KEY` |
| Tunables | `config/flighttrack.yaml` (committed) | search filters, request budget, batch pacing, cooldown steps, alert defaults, health thresholds, retention, default deal feeds |
| What is tracked | the database, edited in the app | trips, flexible searches, deal watches, deal feeds, targets |

### Data model

Existing tables stay: `routes`, `queries`, `observations` (append-only), `alerts_sent`,
`run_log`, `fetch_attempts`, `state`, `deal_posts`, `feed_log`.

Added in M3 (one migration file):

```sql
CREATE TABLE trips (                       -- fixed dates: "when do I book?"
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,             -- used in URLs, e.g. 'japan'
    name TEXT NOT NULL,
    origin TEXT NOT NULL, destination TEXT NOT NULL,      -- IATA
    depart_date TEXT NOT NULL, return_date TEXT,          -- NULL return = one way
    target_price INTEGER,                  -- cents
    max_stops INTEGER,
    status TEXT NOT NULL DEFAULT 'active', -- active | paused | booked | archived
    notes TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE searches (                    -- flexible dates: "which weekend is cheapest?"
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    origin TEXT NOT NULL,
    destinations TEXT NOT NULL,            -- JSON array of IATA codes
    depart_dows TEXT NOT NULL,             -- JSON array, e.g. ["THU","FRI"]
    nights INTEGER NOT NULL,               -- 0 = one way
    window_start TEXT, window_end TEXT,    -- both NULL = rolling horizon
    min_days_ahead INTEGER,
    target_price INTEGER, max_stops INTEGER,
    priority INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'active', -- active | paused | archived
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE watch_queries (               -- which trip or search wants which date pair
    query_id INTEGER NOT NULL REFERENCES queries(id),
    trip_id INTEGER REFERENCES trips(id),
    search_id INTEGER REFERENCES searches(id),
    CHECK ((trip_id IS NULL) <> (search_id IS NULL))
);
CREATE UNIQUE INDEX watch_queries_trip ON watch_queries(query_id, trip_id) WHERE trip_id IS NOT NULL;
CREATE UNIQUE INDEX watch_queries_search ON watch_queries(query_id, search_id) WHERE search_id IS NOT NULL;
CREATE TABLE deal_watches (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    origins TEXT NOT NULL, destinations TEXT NOT NULL, keywords TEXT NOT NULL DEFAULT '[]',  -- JSON arrays
    max_price INTEGER, status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE deal_feeds (
    id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, name TEXT,
    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);  -- JSON values
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | failed
    requested_by TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT
);
```

Added later: `notifications` (M4), `owner_sessions` (M5), `push_subscriptions` (M8).

A route becomes a derived row: one per origin and destination that some trip or search uses.
The target for a date pair is the lowest target among the trips and searches that want it,
and the route's own target only if none of them has one.

### How collecting is scheduled (from M4)

The rule that makes it self-healing: **what to fetch next is worked out from the database, not
from the clock.** A query is due when its `last_fetch_at` is older than its refresh interval.
After a block, a restart or a week of downtime, the worker looks at the same table and carries
on. There is no "today's run" to miss.

- Every Google request counts against one rolling 24-hour budget
  (`collect.daily_request_budget`, default 50), counted from `fetch_attempts`.
- Detail fetches go out in small batches (`collect.batch_size`, default 5) with a random gap
  between batches (`collect.batch_gap_minutes: [25, 45]`) and the existing 4–8 s between
  requests.
- The calendar sweep runs once a day in groups of four calls with the same gaps.
- Order within a batch: (1) queries a person asked for through a job; (2) active trips not
  fetched in 20 h; (3) dates the sweep flagged today; (4) the three cheapest dates of each
  search not fetched in 20 h; (5) everything else, oldest first, at least weekly.
- A block ends the batch and sets `cooldown_until` (the existing 1 h → 4 h → 12 h → 24 h
  steps). The next batch is scheduled for the end of the cooldown plus a random delay.

With the shipped example (154 date pairs), a day is about 12 calendar calls, 1 canary and
30 to 37 detail fetches.

### Reliability rules

Every milestone keeps these true.

1. **Never silent.** Two signals: a push from the app when the collector is unhealthy, and
   `/api/health` answering 503 when the newest price is older than 36 h, which the container
   health check and the status page show. There is no outside monitor, by the owner's choice
   (see the decisions log), so a dead box is noticed on `/status` or by the absence of alerts.
2. **Self-healing.** As described above, plus container health checks,
   `restart: unless-stopped`, and marking interrupted runs and jobs as failed at start-up.
3. **Few requests.** The sweep covers breadth; detail fetches go where a price matters.
4. **Evidence.** Typed failures, every request logged, unreadable pages saved, a daily canary,
   and real captured pages as regression tests.
5. **Data safety.** Append-only observations, numbered migrations, a nightly backup with 14
   kept, and a documented restore.
6. **An alert is a record first and a push second.** Every alert is written to the
   `notifications` table and shown in the app. Push is one way of delivering it.

### API surface (M5, M8)

Public, read-only, rate limited:

| Endpoint | Returns |
|---|---|
| `GET /api/livez` | 200 while the process runs |
| `GET /api/health` | 200, or 503 when the newest price is older than `health.max_hours_without_success` or the worker heartbeat is stale |
| `GET /api/meta` | version, commit, home airport, currency, whether owner sign-in and push are set up |
| `GET /api/overview` | trip summaries (price now, change over 7 days, verdict, target, low, a short series), the best date of each search, recent notifications, recent deals, a health summary |
| `GET /api/trips/{slug}` | the price series, the latest offers, the verdict with reasons, alert history, the Google Flights link |
| `GET /api/searches/{slug}` | per destination: every date pair with price, source (`detail` or `calendar`), age, flags and link |
| `GET /api/queries/{id}/history` | the series for one date pair |
| `GET /api/deals` | matched deal posts |
| `GET /api/status` | health report, recent runs, requests by day and outcome, cooldown, budget used, feeds, next batch, backups, database size |
| `GET /api/airports?q=` | airport search for the forms |

Owner (cookie session; state-changing requests must carry this site's `Origin`):

| Endpoint | Does |
|---|---|
| `GET /api/auth/me`, `POST /api/auth/login`, `POST /api/auth/logout` | Sign-in, copied from Snow Watch |
| `/api/admin/trips`, `/api/admin/searches`, `/api/admin/deal-watches`, `/api/admin/deal-feeds` | List, create, update, change status, delete |
| `POST /api/admin/deal-watches/test` | Check a headline against the watches |
| `GET` and `PUT /api/admin/settings` | Alert thresholds |
| `POST /api/admin/jobs`, `GET /api/admin/jobs/{id}` | "Check now", and its progress |
| `GET /api/push/key`, `POST /api/push/subscribe`, `/unsubscribe`, `/test` | Push (M8), owner only |

## Milestones

### M1. Collector fixes

**Goal.** Everything found broken on 2026-11-14 is fixed in the existing layout, with tests,
so the collector can run unattended on the mini PC while the web app is built.

**Read first.** `src/flighttrack/notify.py`, `deals.py`, `gflights/calendar.py`, `sweep.py`,
`gflights/results.py`, `gflights/transport.py`, `source.py`, `serve.py`, `tests/helpers.py`,
`tests/test_deals.py`, `tests/fixtures/live-20261114/README.md`.

**Tasks.**

1. **Notifications** (`notify.py`).
   - Remove the ntfy channel (`NTFY_TOPIC`, its config section, its docs). A config that still
     names it is refused with a message that says so.
   - In `notify()`, turn any `Exception` from a channel into `NotifyError`.
   - Tests: send the three real subject shapes (`✈ $612 Tokyo · Apr 10`, `✈ deal: …`,
     `⚠ flighttrack is not collecting prices`) through the `file` and `smtp` channels and
     assert they arrive intact (SMTP: RFC 2047-encoded on the wire, decoded back). For
     `alert.run`, `deals.alert` and `health.run`, assert that a channel raising `ValueError`
     produces `delivery_error` or a `False` return, never an exception.
   - Change `notify.channel` in the shipped `config.yaml` to `file`.
2. **Clock-independent tests.** The pattern is already in the suite: pass a fixed `now` to the
   function under test, and a fixed `today` to `make_db` (`tests/helpers.py`).
   - Fix `tests/test_scenarios.py::test_window_reactivates_a_previously_expired_row` (fails
     from 45 days ahead) and `tests/test_page.py::test_page_has_every_section_and_trip_target`
     (fails from 400 days ahead).
   - Give `deals.pending_alerts`, `deals.alert` and `deals.run` a `now` parameter like
     `deals.poll` has, so nothing in `deals.py` needs the real clock under test.
   - Prove it: run the suite with the clock moved 7, 45, 120, 400 and 800 days ahead
     (`time-machine` started from a small pytest plugin outside the repository; do not add it
     as a dependency). All must pass. Put the five result lines in the pull request.
3. **Deal feeds.**
   - Shipped `deals.feeds` becomes `https://www.theflightdeal.com/feed/` and
     `https://www.theflightdeal.com/category/flight-deals/seattle-flight-deals/feed/`. Both
     returned 16 items on 2026-11-14. The same post can appear in both; `deal_posts.guid`
     already prevents a double alert.
   - In `deals.poll`, a body that begins with `<!doctype html` or `<html` (ignoring case and
     leading space) is a failure with the message "feed returned a web page, not a feed".
   - In `health.check`, add a warning when a feed has returned zero items for seven days.
   - The Flight Deal's headlines look like
     `Alaska Air: Seattle – San Diego (and vice versa). $127 (Basic Economy) / $237 (Regular Economy). Roundtrip, including all Taxes`.
     Add that shape to the tests. `price_from_text` takes the first amount, which is right.
4. **Calendar sweep** (`gflights/calendar.py`, `sweep.py`).
   - `build_body` gains `carry_on_bags` and `checked_bags`. When either is non-zero, slot 10 of
     the filter list is `[carry_on_bags, checked_bags]`. `sweep.fetch_window` passes
     `cfg.search.carry_on_bags`.
   - Try to find the slot for "exclude basic economy" using the procedure in
     [Calendar filter slots](#calendar-filter-slots). If it is found, send it and record the
     slot in that appendix. If not, leave the appendix saying so.
   - Rewrite `sweep.promising` so a calendar price is judged only against other calendar
     prices. A date earns a confirming fetch when any of these holds: its calendar price is at
     or under the target; it is at least `calendar.priority_boost_pct` below the median of that
     date pair's own calendar prices over the last 30 days (at least three of them); or it is
     one of the three cheapest dates in its window. Keep `calendar.confirm_budget` as the cap.
   - Store `{"filters": {"bags": true, "exclude_basic": <bool>}}` in the calendar offer's
     `raw` so later readers know what the number means.
5. **Results parser** (`gflights/results.py`, `source.py`).
   - `Itinerary.duration_min`: use `raw[0][9]` when it is a positive integer, otherwise the
     sum of the segments.
   - Swap the bucket labels: rows from `payload[2][0]` are `best`, rows from `payload[3][0]`
     are `other`. Correct the module docstring, the note in `cli.cmd_doctor`, and the two
     places in `docs/data-sources.md` that describe them.
   - Add layovers to `Offer.raw` from `raw[0][13]` (a list whose items start with minutes and
     airport code).
   - Add `results.page_min_price(payload)` returning `payload[25][0][1]` when it is a number.
     In `GoogleHtmlSource.fetch`, when it is present and differs from the lowest parsed
     price, set `raw["page_min"]` on the offers and count it in a new
     `FetchStats.parser_mismatch`. `health.check` warns at three or more in 24 h. In all four
     live samples the two numbers were equal.
6. **Transport** (`gflights/transport.py`, `pyproject.toml`).
   - `impersonate = ["primp>=2,<3"]`.
   - `default_transport("auto")` falls back to `urllib` on any exception from
     `PrimpTransport()`, and prints one warning line.
   - Update `USER_AGENT` to a current desktop Chrome.
7. **Start-up catch-up** (`cli.cmd_serve`). Run once at start when `last_success_at` is
   missing or older than 20 hours. Drop the shipped `fetch.max_queries_per_run` to 25 until
   the home limit has been measured.
8. **Fixture tests.** New `tests/test_live_fixtures.py` asserting every expected value in
   `tests/fixtures/live-20261114/README.md`.
9. **Docs.** Correct the README status table and `docs/data-sources.md` to say what was
   verified live and when.

**Not in this milestone.** Moving files, new dependencies, the web app, the new scheduler.

**Acceptance.**
- `pip install -e '.[dev]' && pytest -q`: all pass, at least 205 tests, and again with the
  clock moved ahead as in task 2.
- `pip install -e . pytest && pytest -q`: all pass without the optional packages.
- `flighttrack doctor --offline` exits 0.
- If Google is reachable: `flighttrack doctor --dest SLC --depart <today+60> --ret <today+63> --calendar`
  passes and the calendar price for the first date is close to the results-page price (equal
  if the exclude-basic slot was found). At most six live requests in the session.
- CI green on the pull request.

### Checkpoint A. Start collecting now (owner, optional but recommended)

History is the one thing that cannot be made up later. After M1 merges, the existing
container can run on the Docker VM, on the LAN only, while the rest is built. The database it
fills carries forward.

1. On the Docker VM:
   ```bash
   git clone https://github.com/alaurin10/flight-tracking.git /opt/flight-tracking
   ```
   ```bash
   cd /opt/flight-tracking && cp .env.example .env
   ```
2. In `docker-compose.yml` change the port line to `"8081:8080"`. In `config.yaml` put your
   real trips, searches and deal watches. Both are local edits; do not commit them.
3. Start it and run the live checks:
   ```bash
   docker compose up -d --build
   ```
   ```bash
   docker compose exec flighttrack flighttrack doctor --calendar
   ```
   ```bash
   docker compose exec flighttrack flighttrack calibrate --count 20 --interval 6 --yes
   ```
4. If `doctor --calendar` printed sensible prices, set `calendar.enabled: true` in
   `config.yaml` and `docker compose restart`.
5. Open `http://<docker-vm-ip>:8081/`. Do not add it to the tunnel yet: this server has no
   sign-in.

Tell the next session what `calibrate` reported (how many requests succeeded before the first
block, if any). M4 uses it for the default budget.

### M2. Restructure to the Snow Watch layout

**Goal.** Same behavior, new skeleton: the layout, tooling, storage layer and container that
the web app will be built on.

**Read first.** Snow Watch: `backend/pyproject.toml`, `backend/snowwatch/store/db.py`,
`settings.py`, `logs.py`, `docker/Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`,
`.github/dependabot.yml`, `.env.example`. This repo: `pyproject.toml`, `db.py`, `schema.sql`,
`compact.py`, `config.py`, `Dockerfile`, `.github/workflows/ci.yml`.

**Tasks.**

1. `git mv src/flighttrack backend/flighttrack`, `tests backend/tests`. New
   `backend/pyproject.toml` modelled on Snow Watch's: Python 3.12, `uv`, a lockfile,
   `ruff` with the same rule set, `pytest`. Dependencies: `pyyaml`, `primp`,
   `pydantic`, `pydantic-settings`; `fast-flights==3.1.0` stays an optional extra that the
   image installs.
2. `ruff format` the tree in a commit of its own, then fix `ruff check`.
3. `mypy` with `strict = true` and an `ignore_errors` override listing each existing module.
   New modules are never added to the list; a later milestone that changes a listed module
   removes it from the list.
4. `backend/flighttrack/store/db.py` copied from Snow Watch: `connect(path, readonly=False)`
   with WAL, `busy_timeout=5000`, `foreign_keys=ON`; `migrate()` applying
   `store/migrations/NNN_*.sql` in order and recording them in `schema_migrations`.
   - `001_init.sql` is today's `schema.sql` plus the three columns in
     `db.COLUMN_MIGRATIONS`, written with `IF NOT EXISTS` throughout.
   - Adopting an existing database: when `observations` exists and `schema_migrations` is
     empty, first add any missing columns the old way, then record `001_init` as applied
     without running it.
   - `compact.py` re-creates the delete trigger from a constant instead of re-reading
     `schema.sql`.
   - Keep `utcnow()`, `parse_ts`, `get_state`, `set_state` where callers can import them.
5. `backend/flighttrack/settings.py` (pydantic-settings, reads `.env`): `env`, `data_dir`,
   `db_path` (default `<data_dir>/flights.db`), `config_dir`, `static_dir`, `home_airport`,
   `owner_password`, `public_url`, `trusted_proxy`, `contact_email`, the two VAPID keys,
   `serpapi_key`, `git_commit`. Unused ones are harmless until their
   milestone. Everything the app writes goes under `data_dir`, because the container's root
   file system is read-only: the page at `<data_dir>/out/index.html`, failure pages at
   `<data_dir>/failures`.
6. Split the YAML. `config/flighttrack.yaml` holds the tunable sections (`search`, `fetch`,
   `calendar`, `alerts`, `health`, `retention`, `horizon`, default `deals.feeds`).
   `config.load` reads it, and still accepts a second file with `home`, `routes`, `trips`,
   `patterns` and `deals.watches` (`--config`), because M3 has not replaced that yet. The
   database and failure-directory paths come from `Settings`.
7. `logs.py`: one JSON object per line, as Snow Watch does. Replace `print` in the long-running
   paths (`fetch.run`, `sweep.run`, `deals`, `health`) with logging; command output that a
   person reads (`doctor`, `status`, `report`, `advise`) stays as printed text.
8. `docker/Dockerfile` (Python stage only for now, base image pinned by digest, uid 1000,
   `GIT_COMMIT` build argument) and `docker-compose.yml` with Snow Watch's hardening block
   (`read_only`, `tmpfs: /tmp`, `cap_drop: [ALL]`, `no-new-privileges`). One service for now,
   still running `flighttrack serve`, `./data:/data`, port `8091:8080`.
9. CI rewritten after Snow Watch's `backend`, `audit` and `docker` jobs, with actions pinned
   to commits. Add `.github/dependabot.yml`.
10. `.env.example` in Snow Watch's commented style. Update `README.md` install and
    development sections. Leave the deployment docs for M5.

**Not in this milestone.** New tables, FastAPI, APScheduler, any change in what is fetched.

**Acceptance.**
- `cd backend && uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy flighttrack && uv run pytest`: all pass.
- A test opens a database created by the M1 code (build one in the test from the old
  `schema.sql` text kept as a fixture), runs `migrate()`, and finds `001_init` recorded, every
  row intact and the append-only triggers present.
- `docker build -f docker/Dockerfile .` succeeds and `docker run --rm <image> flighttrack doctor --offline` exits 0.
- If checkpoint A happened: the pull request says how to move over (stop the old container,
  `sudo chown -R 1000:1000 data out`, pull, `docker compose up -d --build`).

### M3. Database as the source of truth

**Goal.** Trips, flexible searches and deal watches live in the database. Nothing personal is
left in the repository.

**Read first.** `expand.py`, `config.py` (the `Route`, `Pattern`, `Trip`, `DealWatch` classes
and their validation), `alert.find_candidates`, `sweep.plan`, `deals.first_match`,
`report.grid` and `report.advise`, `html.trip_cards`; Snow Watch `locations/service.py` and
`store/repo.py` for the service style.

**Tasks.**

1. Migration `002_watches.sql` with the tables under [Data model](#data-model).
2. `backend/flighttrack/watches.py`: typed models (pydantic) and functions to list, get,
   create, update, set status and delete trips, searches, deal watches and deal feeds. Reuse
   the checks now in `config.py`: three-letter airport, destination differs from origin, ISO
   dates, return after departure, weekdays, `nights` at least 0, cents not negative, unique
   slug (derived from the name when not given).
   Limits, enforced on save: 20 active trips, 10 active searches, 400 active date pairs in
   total. The error says how many pairs the change would create.
   Airport codes are checked against `backend/flighttrack/data/airports.csv`, built from the
   public-domain OurAirports list: rows with an IATA code and scheduled service; columns
   `iata,name,city,country`. Commit the small build script.
3. `expand.py` reads the database.
   - Routes are derived: upsert one per origin and destination in use, labelled with the
     airport's city; deactivate those no longer used.
   - For each active trip and search, upsert the `queries` rows and the `watch_queries`
     links. Remove links that no longer apply. A query with no links is deactivated, never
     deleted. `queries.pattern` keeps being written with the slug, for the reports.
   - `expand` runs inside `watches` after every change, so the grid is correct the moment a
     trip is saved.
4. Target lookup in one place: `watches.target_for(conn, query_id)` implementing the rule
   under Data model. `alert.find_candidates` and `sweep` use it; `expand.trip_targets`
   goes away.
5. `sweep.plan` and `deals` read searches, watches and feeds from the database.
   `REGIONS` stays in code.
6. `flighttrack import-config <file>`: reads the old YAML (`home`, `routes`, `trips`,
   `patterns`, `deals`), creates what is missing, updates what exists by slug, never deletes.
   A pattern with `routes: [HND, NRT]` becomes one search with two destinations. A route's
   `target_price` and `priority` carry onto the searches that use it. Print a summary.
   Also `flighttrack export-config` writing the same shape, for backups.
7. Delete the root `config.yaml`. Add `config/seed.example.yaml` with invented trips. Update
   `demo.py` and the test helpers to create watches through `watches.py`.
8. `report.py` and `html.py` read trips and searches from the database, so the page still
   works.

**Not in this milestone.** HTTP endpoints, the scheduler.

**Acceptance.**
- `uv run pytest` passes, including: importing the old shipped config (kept as a test
  fixture) yields 154 active queries with a fixed date; importing twice changes nothing;
  pausing a search deactivates only the queries no other watch wants; saving a trip that
  would exceed a limit is refused with the count in the message.
- A database with history from before this milestone still shows the same history for the
  same date pairs after migration and import.
- `git grep -n "2027-04-10"` finds nothing outside `backend/tests`.

### M4. The worker

**Goal.** One long-running process that collects on its own, recovers on its own, and says so
when it cannot.

**Read first.** Snow Watch `scheduler.py` (the `Worker` class, heartbeat, the run lock;
skip its Healthchecks pings), `jobs.py`, `store/backup.py`, `store/retention.py`, `cli.py`
(`worker`, `healthcheck`). This repo: `fetch.py` (`select_due`, `run`, cooldown functions), `sweep.py`,
`jobs.py`, `serve.py`, `health.py`, `compact.py`.

**Tasks.**

1. Dependency: `apscheduler>=3.10,<4`. Rename today's `jobs.py` to `cycle.py` (the staged
   run), and copy Snow Watch's `jobs.py` queue (`enqueue`, `claim_next`, `finish`,
   `pending`) with a JSON `payload`.
2. `fetch.select_due` gains the order described under
   [How collecting is scheduled](#how-collecting-is-scheduled-from-m4). Add
   `fetch.budget_remaining(conn, cfg, now)` counting Google requests in `fetch_attempts`
   over the last 24 hours. Keep `PRIORITY_INTERVAL_DAYS` as the floor for rotation.
3. `scheduler.py`, class `Worker`, with one lock so a batch, a sweep and a job never overlap:

   | Job | When | Does |
   |---|---|---|
   | tick | every minute | If no cooldown, budget remains and `next_batch_at` has passed: run one batch through `fetch.run(limit=batch_size)`, then alerts, then set `next_batch_at`. After a block, `next_batch_at` is `cooldown_until` plus 5–20 minutes |
   | sweep | daily at `collect.sweep_at` local, and at start if none in 26 h | Calendar calls in groups of four, a batch gap between groups, then mark candidates |
   | deals | every 3 h | `deals.run` |
   | health | hourly | `health.run`; notifies through the notifier |
   | canary | daily | See task 4 |
   | job queue | every 15 s | `fetch_trip`, `fetch_search`, `poll_deals`, `run_batch`; a fetch job puts that trip's queries first in the next batch and runs it now if the budget allows |
   | backup | nightly | `VACUUM INTO data/backups/flights-YYYYMMDD.db` plus `export-config` output, keep 14 |
   | retention | nightly | today's `compact.compact`, plus `notifications`, `jobs` and `run_log` older than 180 days |
   | vacuum | weekly | |
   | heartbeat | every 30 s | touch `data/worker.heartbeat` |

   At start: run migrations, mark unfinished `run_log` rows and running jobs as failed, run
   `expand`, schedule the first batch 1–5 minutes out.
4. `canary.py`: one request a day for a busy nonstop route from the home airport (default
   `LAX`, one way, 30 days out; `collect.canary_dest`). It passes when the page is results,
   there are at least five itineraries, every price is plausible, both lists are present,
   `page_min_price` equals the lowest parsed price, and at least 80% of itineraries have an
   airline, times and a duration. A failure saves the page to the failure directory, records
   `layout_drift` in `state`, and `health.check` reports it as critical. A pass clears it.
5. `notifier.py` replaces `notify.py` at the call sites. `Notifier.send(kind, title, body, url)`
   with `kind` one of `price`, `deal`, `health`; it always writes a row to a new
   `notifications` table (`id, kind, title, body, url, created_at`), then hands the message
   to each configured channel. Channels for now: the `file` and `smtp` channels from
   `notify.py`, when configured. A channel failing
   never loses the row. `alerts_sent` is written when the row is written.
   Health also sends one `health` notification when the collector recovers.
6. `config/flighttrack.yaml` gains `collect:` (`daily_request_budget: 50`, `batch_size: 5`,
   `batch_gap_minutes: [25, 45]`, `sweep_at: "04:10"`, `trip_refresh_hours: 20`,
   `top_dates_per_search: 3`, `canary_dest: LAX`). If checkpoint A reported a measured limit,
   set the budget to half of it, and no higher than 80.
7. Commands: `flighttrack worker`, `flighttrack healthcheck worker` (exit 0 when the
   heartbeat is under three minutes old). `flighttrack run` stays as a one-off full cycle
   for debugging. Compose gains a `worker` service with that health check; `serve` keeps
   serving the page until M5.

**Not in this milestone.** FastAPI, push.

**Acceptance.**
- Tests with an injected clock and `FakeSource`, no sleeping:
  a block mid-batch sets the cooldown and the next batch is scheduled after it; when the
  cooldown passes the remaining queries are fetched; after seven simulated days of downtime
  trips are fetched first and the budget is never exceeded in any 24-hour window; a worker
  killed mid-run marks the run failed at the next start; a job for a trip is fetched within
  one tick; the canary failing raises a critical health problem and passing clears it; a
  notifier channel that raises still leaves the notification row.
- `docker compose up -d worker` with `FLIGHTTRACK_SOURCE=fake` (add this switch for tests and
  demos) stores observations within two minutes and reports healthy.
- `uv run ruff check . && uv run mypy flighttrack && uv run pytest` pass.

### M5. API and deploy kit

**Goal.** A FastAPI service that is safe on the public internet, and everything needed to put
it on the mini PC. Two pull requests are fine: the API, then the deploy kit.

**Read first.** Snow Watch `api/app.py` (headers, CSP, `livez`, `health`, static files, SPA
fallback), `api/auth.py`, `api/ratelimit.py`, `api/admin.py`, `api/models.py`, `cli.py`
(`schema`, `owner`), `deploy/`, `docs/DEPLOYMENT.md`, `docs/deploy-notes-home.md`, the
`docker` job in CI. This repo: `report.py`, `html.py`, `health.py`, `serve.status_payload`,
`watches.py`.

**Tasks, API.**

1. Dependencies: `fastapi`, `uvicorn[standard]`. `api/app.py` with `create_app(settings)` and
   `production_app()`.
2. Copy `api/auth.py` and `api/ratelimit.py` from Snow Watch (cookie `fw_owner`, migration
   `owner_sessions`, minimum password 12 characters, lockout that doubles, `Origin` check,
   `flighttrack owner logout-all`).
3. Public endpoints as listed under [API surface](#api-surface-m5-m8), in `api/public.py`,
   with pydantic response models in `api/models.py`. Build them on the existing queries:
   `report.grid`, `report.history`, `report.advise`, `health.check`, and the SQL in
   `serve.status_payload` and `html.collector_section`. Prices are integer cents. Times are
   ISO 8601 UTC. Every endpoint opens a read-only connection for the request.
4. Admin endpoints in `api/admin.py` calling `watches.py`. Creating or changing a trip or
   search queues a fetch job for it. Validation errors return 422 with a message a person can
   read.
5. Rate limits: public reads 60 a minute per client, `/api/airports` 30 a minute, sign-in by
   the login throttle.
6. Headers on every response, as Snow Watch sets them: a strict Content-Security-Policy,
   `nosniff`, frame denial, referrer policy, permissions policy, HSTS in production; plus
   `X-Robots-Tag: noindex, nofollow` and a `robots.txt` that disallows everything. The site is
   public but should not be in search results.
7. `flighttrack schema --out` writing the response models as JSON Schema, for M6.
8. Until M6: `GET /` returns the existing page from `html.render`, cached for 60 seconds.
9. Compose: `api` (`uvicorn --factory flighttrack.api.app:production_app`, port `8091:8000`,
   `mem_limit: 256m`) and `worker` (`mem_limit: 384m`), same image, `ENV: prod`.
   Remove `serve.py` and its tests.

**Tasks, deploy kit.**

10. `deploy/deploy.sh`, `deploy/flightwatch-deploy.service`, `deploy/flightwatch-deploy.timer`
    copied from Snow Watch with the paths and the watch list (`backend frontend config docker docker-compose.yml`) changed.
11. `docs/DEPLOYMENT.md` in Snow Watch's structure: get the code and settings; start it;
    HTTPS in front; monitoring (the status page, `/api/health` and the health push; leave out
    Snow Watch's Healthchecks.io and Uptime Kuma parts); automatic updates; everyday tasks;
    backups and restore; rolling back; troubleshooting. Include the first-run live checks (`doctor`,
    `doctor --calendar`, `calibrate`) run with `docker compose run --rm worker flighttrack …`.
12. `docs/deploy-notes-home.md` for this installation: Docker VM, `/opt/flight-tracking`,
    port 8091, the `additional_hosts` entry for `flights.andrewlaurin.com`, moving over from
    checkpoint A.
13. Delete `deploy/lxc/`, `deploy/com.flighttrack.daily.plist`, `deploy/crontab.example`,
    `deploy/flighttrack.service`, `deploy/flighttrack.timer`, `docs/running.md`,
    `docs/deploy-proxmox.md`, the root `Dockerfile`. Rewrite `docs/using.md` only where it is
    now wrong.
14. CI `docker` job: the smoke test from Snow Watch adapted. Start the image; `livez` is 200;
    `health` is 503; seed with `flighttrack demo`; `health` is 200; `/api/overview` lists the
    demo trip; the CSP header is present; sign in with a test password and create a trip
    through `/api/admin/trips`.

**Not in this milestone.** The SvelteKit app, push.

**Acceptance.**
- API tests with FastAPI's `TestClient` for every endpoint, including: admin without a
  session is 401; a write with a foreign `Origin` is 403; five wrong passwords lock out;
  `health` flips between 200 and 503 with the age of the newest observation.
- `uv run flighttrack schema` output is committed and CI fails when it is stale.
- `docker compose up -d --build` on a clean checkout with only `OWNER_PASSWORD` set brings
  both services to healthy.
- The CI smoke test passes.

### Checkpoint B. Go live (owner)

Follow `docs/deploy-notes-home.md`, written in M5. In short:

1. If checkpoint A is running: `docker compose down`, then
   `sudo chown -R 1000:1000 data`, and keep your edited `config.yaml` somewhere safe.
2. `git pull` (or clone to `/opt/flight-tracking`), `cp .env.example .env`, and set
   `OWNER_PASSWORD` (12 or more characters), `PUBLIC_URL=https://flights.andrewlaurin.com`,
   `TRUSTED_PROXY=1`, `TZ=America/Los_Angeles`, `HOME_AIRPORT=SEA`.
3. Start it, then load your trips:
   ```bash
   GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
   ```
   ```bash
   docker compose run --rm -v "$PWD/config.yaml:/tmp/config.yaml:ro" worker flighttrack import-config /tmp/config.yaml
   ```
4. Live checks from the home address:
   ```bash
   docker compose run --rm worker flighttrack doctor --calendar
   ```
   ```bash
   docker compose run --rm worker flighttrack calibrate --count 20 --interval 6 --yes
   ```
   If the measured limit is well above or below 100 requests, set
   `collect.daily_request_budget` to about half of it.
5. Home Assistant → Settings → Apps → Cloudflared → Configuration, add under
   `additional_hosts` and restart the app:
   ```yaml
   - hostname: "flights.andrewlaurin.com"
     service: "http://<docker-vm-ip>:8091"
   ```
   Do not create a Cloudflare Access application for it; the app has its own sign-in.
6. Install the deploy timer (three commands in `docs/DEPLOYMENT.md`).
7. On a phone with Wi-Fi off: the site opens, the sign-in works, and the router still has no
   port-forward rules.

### M6. Frontend: the read-only app

**Goal.** The SvelteKit app replaces the static page. Anyone with the link can read; nothing
can be changed yet.

**Read first.** Snow Watch `frontend/` whole: `package.json`, `svelte.config.js`,
`vite.config.ts`, `eslint.config.js`, `prettier.config.js`, `tsconfig.json`,
`scripts/gen-types.mjs`, `src/app.html`, `static/theme-init.js`, `src/lib/theme.css`,
`src/lib/api.ts`, `src/lib/prefs.svelte.ts`, `src/lib/charts/`, `e2e/serve.sh`,
`playwright.config.ts`, the `frontend` and `e2e` CI jobs, the Node stage of `docker/Dockerfile`.
This repo: `html.py` (the layout and wording being replaced), the API schema.

**Tasks.**

1. Scaffold `frontend/` from Snow Watch's configuration files with the map libraries removed.
   Svelte 5, static adapter, generated types from `api-schema.json` (`npm run types`), vitest,
   Playwright, eslint, prettier. Keep the Inter and Manrope fonts and the theme tokens so the
   two sites feel related; choose a different accent colour.
2. Pages:

   | Route | Shows |
   |---|---|
   | `/` | Trip cards (price now, change this week, verdict pill, target, all-time low, small chart); the best date of each flexible search; recent alerts from `notifications`; recent deals; a collector health pill linking to Status |
   | `/trip/[slug]` | Price history chart with the target drawn on it; the verdict and its reasons; the latest offers (airline, stops, times, total time, layovers); alert history; "Open in Google Flights" |
   | `/search/[slug]` | One calendar heat map per destination, cheapest outlined, under-target and at-low marks; a table of the same data sorted by price; tapping a date shows its history. Calendar-sourced prices are marked as estimates |
   | `/deals` | Matched deal posts with price, watch and link |
   | `/status` | Health problems in plain words, last success, next batch, cooldown, budget used today, requests per day by outcome for 14 days, feeds, backups, version |
   | `/more` | Theme, about, sign-in link |

3. Charts are hand-built SVG on d3 scales, as in Snow Watch: one y-axis, nothing drawn
   outside the plot, usable at 380 px wide with a thumb, keyboard reachable, a table twin for
   each chart.
4. States: loading, empty ("No prices yet. The first ones arrive within a few minutes of
   adding a trip."), stale data, and API unreachable.
5. Dockerfile gains the Node build stage; the API serves the build with the SPA fallback and
   the hashed start-up script in the CSP, as in Snow Watch. Remove `html.py`, the `html` and
   `demo` page output, and the interim `/` handler. Keep `demo.seed`.
6. CI: `frontend` job (types current, lint, check, unit tests, build) and `e2e` job. For
   e2e, `frontend/e2e/serve.sh` seeds a database with `demo.seed` and starts the API on it.
   Specs: overview renders the seeded trip; trip page chart and table agree; search grid opens
   a date; status shows healthy; a 380 px viewport has no horizontal scroll; dark theme.

**Not in this milestone.** Forms, sign-in pages, service worker, push.

**Acceptance.**
- `cd frontend && npm ci && npm run types && npm run lint && npm run check && npm test && npm run build && npm run e2e` pass.
- Screenshots of each page, phone and desktop, light and dark, attached to the pull request.
- `docker compose up -d --build` serves the app at `:8091`; the CI smoke test checks `/` and
  `/status` return the app shell.

### M7. Manage

**Goal.** The owner adds, changes and removes what is tracked from the browser, and sees a
first price within about a minute.

**Read first.** Snow Watch `frontend/src/lib/manage/`, `frontend/src/routes/admin/`,
`src/lib/session.svelte.ts`, `src/lib/modal.ts`. This repo: `api/admin.py`, `watches.py`.

**Tasks.**

1. Sign-in page and session state copied from Snow Watch; "Manage" appears in the navigation
   for everyone and asks for the password when opened.
2. `/manage`: lists of trips, flexible searches, deal watches and feeds, each with status and
   edit, pause, resume, archive, delete (delete asks first; history is kept either way).
3. Forms:
   - Trip: name, from, to (airport search on `/api/airports`), depart, return or one way,
     target price, maximum stops, notes. Mark as booked.
   - Flexible search: name, from, one or more destinations, weekdays, nights, date window or
     rolling, target, maximum stops. Before saving, show "This tracks N date pairs" and how
     many days a full detail refresh takes at the current budget.
   - Deal watch: name, origin words, destination words or a region, maximum price, with a
     "try a headline" box using `/api/admin/deal-watches/test`.
   - Deal feed: URL; on save the server fetches it once and refuses a URL that is not a feed.
   - Money is typed in dollars and sent in cents.
4. "Check now" on a trip or search queues a job and shows queued, running, done or the
   failure reason, polling `/api/admin/jobs/{id}`. During a cooldown it says when collecting
   resumes instead of queuing.
5. Settings: the alert thresholds from `config/flighttrack.yaml` that make sense to change
   (`dedup_days`, `dedup_further_drop_pct`, percentile alerts on or off), stored in
   `settings`.
6. Unit tests for the forms' validation; e2e: sign in, add a trip against a fake source,
   see its first price, pause it, delete it; a wrong password shows the error.

**Not in this milestone.** Push, service worker.

**Acceptance.** The frontend and backend check commands pass; the e2e flow above passes in
CI; on a phone-sized viewport every form is usable without zooming.

### M8. Push alerts and the installable app

**Goal.** Price alerts, deal alerts and health warnings arrive as push notifications on the
owner's devices. The old `file` and `smtp` channels are gone.

**Read first.** Snow Watch `push.py`, `api/push_api.py`, migration `003_m6.sql` (the
subscription table), `frontend/src/service-worker.ts`, `src/lib/push.ts`,
`static/manifest.webmanifest`, `scripts/make-icons.mjs`, `src/lib/update.svelte.ts`, and the
push section of `docs/DEPLOYMENT.md`.

**Tasks.**

1. Dependency `pywebpush`. Copy `push.py` (keys, subscribe, unsubscribe, send, pruning of
   dead subscriptions, the allow-list of browser push services) and `api/push_api.py`.
   Subscribing, changing preferences and the test push require the owner session; the
   subscription cap is 20.
2. `flighttrack push keys` prints the two `.env` lines.
3. `notifier.py` gains the push channel. Messages:

   | Kind | Title | Body | Opens |
   |---|---|---|---|
   | price | `$612 Tokyo · Apr 10 – Apr 24` | the reason line from `alert.Candidate.context` | `/trip/<slug>` or `/search/<slug>` |
   | deal | the headline, shortened | watch name and price | `/deals` |
   | health | `Flight Watch has stopped collecting prices` or `…is collecting again` | the critical problems | `/status` |

   Each device can turn each kind on or off. Health is on by default.
4. Service worker and manifest: installable, app shell cached, last API answers available
   offline, push shown, tapping a notification opens its page, a new version waits for
   "Reload". Icons generated with the script.
5. Settings → Alerts in the app: turn on, per-kind switches, "Send a test", and the iPhone
   note (install to the Home Screen first, iOS 16.4 or later).
6. Remove the SMTP and `file` channels, the `notify:` config section, the `SMTP_*` settings
   and their docs. Keep a logging channel for tests and development.
7. `docs/DEPLOYMENT.md` gains the push section; `.env.example` the VAPID lines.

**Acceptance.**
- Backend tests with a fake sender: a price alert reaches only devices with `price` on; a
  410 from the push service deletes that subscription; a failed push never blocks the
  notification row or the alert record; a non-owner cannot subscribe.
- e2e: Alerts settings shows "not set up on the server" without keys, and the switches with
  keys.
- Manual, recorded in the pull request: a test push received on a desktop browser.

### Checkpoint C. Turn on alerts (owner)

1. ```bash
   docker compose run --rm worker flighttrack push keys
   ```
   Paste the two lines into `.env`, set `CONTACT_EMAIL`, then `docker compose up -d`. Never
   change the keys afterwards.
2. On each device: open the site, sign in, Settings → Alerts → Turn on alerts → Send a test.
   On an iPhone, add the site to the Home Screen first and open it from there.

### M9. Hardening and 1.0

**Goal.** Close what two weeks of real running showed, and document the finished system.

**Tasks.**

1. Review `/status` and the logs from the soak: request outcomes, any blocks, canary results,
   budget use, alert volume. Tune `collect.*` and alert defaults from what was observed, and
   record the numbers in `docs/SOURCES.md`.
2. Layout-drift drill: feed the worker a results page with a shifted index (a doctored
   fixture) and confirm the canary fails, the page is saved, health turns critical, a push
   goes out, and `fast_flights` keeps prices flowing where it can.
3. Source failover review. Confirm the chain order and that `SERPAPI_KEY` enables the paid
   source for trips only (not grids) when the home address has been blocked for more than
   24 hours. Check SerpApi's current plans and state the cost in `docs/SOURCES.md`.
4. Restore drill on a copy: stop, copy a backup over `flights.db`, start, verify. Fix the
   instructions if any step surprised.
5. Security pass using Snow Watch's `SECURITY.md` and its architecture security section as
   the checklist: every write needs the owner and the `Origin`; every public endpoint is
   bounded and rate limited; the CSP has no inline exceptions beyond the hashed script; no
   secret is logged.
6. Write `docs/ARCHITECTURE.md` and `docs/SOURCES.md` (replacing `docs/data-sources.md`),
   rewrite `README.md` with screenshots, add `CHANGELOG.md`, `SECURITY.md`, `LICENSE`.
7. Remove the `ignore_errors` overrides that remain in the mypy configuration, or list in the
   decisions log why one stays.
8. Tag `v1.0.0`.

**Acceptance.** All check commands pass; the drills are written up in the pull request;
`/api/status` shows a stored price at least every 36 hours over the last seven days.

## Not planned for 1.0

Booking; accounts for other people; different cabins or passenger counts per trip (the
`search:` settings apply to everything so that one price series means one thing); multi-city
trips; nearby-airport searches; other price sources (Travelpayouts, airline sites); hotel or
car prices.

## Appendices

### Live requests

- Use `flighttrack doctor`. It makes one results request, and one calendar request with
  `--calendar`.
- At most 10 requests to Google in a session, at least 7 seconds apart. Stop at the first
  HTTP 429 or `/sorry/` and do not retry in that session.
- Never run `calibrate` from a cloud session. It exists to measure the home address.
- A cloud session being blocked says nothing about the home address. A cloud session
  succeeding does prove the request and the parser are right.
- To save a new fixture, keep only the `<script class="ds:1">…</script>` block (see
  `tests/fixtures/live-20261114/README.md`), in a new dated folder, with its expected values.

### Results payload notes

Positions in the `ds:1` data array, as observed on 2026-11-14. `results._at` returns `None`
for a missing position, so treat every one of these as "check, then use".

| Position | Holds |
|---|---|
| `payload[2][0]` | "Top departing flights" rows |
| `payload[3][0]` | "Other departing flights" rows |
| `payload[7][0]` | `[[null, min], [null, max]]` price range before bag and fare filters |
| `payload[7][1][1]` | airline code and name pairs |
| `payload[25]` | `[[null, cheapest shown]]` |
| `row[0][0]`, `row[0][1]` | first airline code, airline names |
| `row[0][2]` | segments. In a segment: 3 origin, 6 destination, 8 and 10 departure and arrival time, 11 minutes, 17 aircraft, 20 and 21 dates, 22 `[code, number]` |
| `row[0][9]` | total minutes including layovers |
| `row[0][13]` | layovers: `[minutes, airport, …]` each |
| `row[1][0][1]` | price in whole currency units |

For a round trip the page lists outbound flights, each priced at the cheapest round-trip
total that includes it.

### Calendar filter slots

The request body is `f.req=[null, "<json>"]` where the inner JSON is
`[null, FILTERS, [start, end], null, [nights, nights]]`.

| `FILTERS` slot | Meaning | Status |
|---|---|---|
| 2 | trip type (1 round trip, 2 one way) | in use, works |
| 5 | cabin | in use, works |
| 6 | passengers `[adults, children, infants on lap, infants in seat]` | in use, works |
| 10 | bags `[carry_on, checked]` | **verified 2026-11-14**: `[1, 0]` moved Jan 15 from $304 to $347, the carry-on-only results price. Sent since M1; the 2026-11-15 check again gave $347 for Jan 15 |
| 13 | segments | in use, works |
| 17 | `1` | in use |
| 19, 22 | unknown | setting each to `1` changed nothing |
| ? | exclude basic economy | **not found yet**; not searched for in M1 (see the decisions log) |

Finding the exclude-basic slot, in order of preference:

1. From a real browser. Open a search with "Economy (exclude Basic)" selected, open
   DevTools → Network, click **Price graph**, find the `GetCalendarGraph` request, copy the
   `f.req` form value, URL-decode it and compare it with `build_body`'s output for the same
   search. The extra non-null slot is the answer. (Headless Chromium in the cloud sandbox
   rendered the page but the click did not fire the request, so this needs a normal browser.)
2. By trial, at most eight requests: on a route and date where basic and regular economy
   differ clearly (SEA→SLC did: $304 against $394), set one candidate slot at a time and
   look for the first date's price moving to the exclude-basic results price. Extend the list
   with `null` to reach slots above 23.

Until it is found, calendar prices include basic economy while the tracked series does not.
That is why `sweep.promising` compares calendar prices only with other calendar prices, and
why the app marks them as estimates.

### Deal feeds checked on 2026-11-14

| Feed | Result |
|---|---|
| `theflightdeal.com/feed/` | Works. 16 items, US cities, prices in the headline |
| `theflightdeal.com/category/flight-deals/seattle-flight-deals/feed/` | Works. 16 items, Seattle only |
| `secretflying.com/feed/` and its category feeds | Dead: redirect to the HTML home page |
| `fly4free.com/feed/` | Works but Europe-origin, prices in € and £ |
| `travelpirates.com/feed` | Works. Mixed travel posts; a few US flight deals |
| `thriftytraveler.com/feed/` | Works. Articles, not deals |
| `reddit.com/r/flightdeals/new/.rss` | Works (Atom). Community posts, uneven quality |
| `going.com`, `airfarewatchdog.com`, `thepointsguy.com` | No feed |

### Snow Watch reference map

| Need | Copy from `alaurin10/Snow-Forecast` |
|---|---|
| Connection, WAL, migrations | `backend/snowwatch/store/db.py`, `store/migrations/` |
| Settings from `.env` | `backend/snowwatch/settings.py`, `.env.example` |
| JSON logs | `backend/snowwatch/logs.py` |
| Worker, heartbeat, run lock | `backend/snowwatch/scheduler.py` (not its Healthchecks pings) |
| Job queue | `backend/snowwatch/jobs.py` |
| Backup, retention | `backend/snowwatch/store/backup.py`, `store/retention.py` |
| App factory, headers, CSP, health, SPA fallback | `backend/snowwatch/api/app.py` |
| Owner sign-in, lockout, Origin check | `backend/snowwatch/api/auth.py`, `api/ratelimit.py`, migration `004_owner_sessions.sql` |
| Admin CRUD style | `backend/snowwatch/api/admin.py`, `locations/service.py` |
| Response models and schema export | `backend/snowwatch/api/models.py`, `cli.py` (`schema`) |
| Web push | `backend/snowwatch/push.py`, `api/push_api.py`, `frontend/src/lib/push.ts`, `frontend/src/service-worker.ts` |
| Image and compose | `docker/Dockerfile`, `docker-compose.yml` |
| Auto-deploy | `deploy/` |
| CI | `.github/workflows/ci.yml`, `.github/dependabot.yml` |
| Frontend scaffold, theme, client, charts, e2e | `frontend/` |
| Docs structure | `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`, `docs/deploy-notes-home.md`, `SECURITY.md` |
