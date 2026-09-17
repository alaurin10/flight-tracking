# flighttrack

A self-hosted price sensor for the flights you actually care about. It answers three questions:

1. **My dates are set — when do I book?** A fixed trip is tracked daily to departure; you get a
   push when it hits your target, an all-time low, or the cheapest slice of the route's history,
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

| Piece | State |
|---|---|
| Own Google Flights client (no third-party scraper) | ✅ built; encoder byte-identical to a live-proven library; **live call verified by `doctor` on your box** |
| Fallback parser (`fast-flights`), paid API escape hatch (SerpApi) | ✅ optional, in the same chain |
| Calendar sweep (whole grid daily, ~12 calls) | ⚠️ experimental, off by default — `doctor --calendar` first |
| Typed failures, cooldown state, request log, failure artifacts | ✅ |
| Health watchdog with push notification | ✅ |
| Fixed trips, windowed multi-weekday patterns, route filters | ✅ |
| Deal feeds (RSS) | ✅ enabled in the shipped config |
| Book-or-wait advisor, price-sorted reports, `ingest` | ✅ |
| Report: trip cards with history charts and verdicts, best-of-grid, calendar heatmaps, deals, collector | ✅ light/dark, phone-first, no external requests |
| `serve`: scheduler + report server in one process; Docker Compose; launchd; systemd | ✅ see `docs/running.md` |
| `demo`: synthetic history to see the UI today | ✅ |
| Rate-limit calibration | ⬜ `flighttrack calibrate` on the always-on box |

This was built in an environment whose egress policy blocks Google, so the live request is the
one thing not verified here. Everything around it is: 187 offline tests, passing with and without
the optional dependencies. **`docs/data-sources.md`** is the full analysis of every data source
considered and why the system is layered the way it is.

### Where it runs

On an always-on box at home with a residential IP: a mini PC, NAS or Raspberry Pi
running `docker compose up -d`, which starts one process that runs the daily job
at 03:15 and serves the report on port 8080 (Tailscale for your phone away from
home). A Mac works with the launchd agent in `deploy/` if it stays awake. Cloud
IPs get captcha'd. **`docs/running.md`** compares the options; **`docs/deploy-proxmox.md`**
is the Proxmox LXC runbook (`deploy/lxc/`); **`docs/using.md`** is the day-to-day guide.
Storage is light: one SQLite file, ~100 MB/year unbounded or ~35 MB bounded with the default
weekly retention (`flighttrack compact`), which never touches the cheapest-offer price series.

```bash
flighttrack demo && open out/demo.html   # see the report with synthetic data, right now
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

Python 3.10+.

```bash
git clone https://github.com/alaurin10/flight-tracking.git
cd flight-tracking
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[full]'       # core + primp (browser TLS fingerprint) + fast-flights fallback
# pip install -e .             # core only: PyYAML is the sole dependency
```

```bash
export NTFY_TOPIC="pick-something-long-and-random"   # see Notifications
flighttrack expand          # build the date grid — no network
flighttrack doctor          # one real request
flighttrack run             # the daily job
flighttrack report --pattern ski_weekend --sort price
flighttrack advise --pattern japan
```

---

## Configuration

Everything lives in `config.yaml`, laid out around the three questions. There is deliberately
no settings UI: config changes are rare, and a YAML file is diffable and editable over SSH.

```yaml
routes:                      # every trip/pattern references one; carries label, target, priority
  - {dest: HND, label: Tokyo, target_price: 65000, priority: 1}
  - {dest: SLC, label: Salt Lake City, target_price: 14000, priority: 1}

trips:                       # 1. fixed dates — tracked daily to departure, own target
  - {name: japan, dest: HND, depart: 2027-04-10, return: 2027-04-24, target_price: 80000}

patterns:                    # 2. recurring shapes
  - name: ski_weekend
    depart_dow: FRI          # or a list: [THU, FRI]
    nights: 3
    routes: [SLC]
    window: {start: 2027-01-01, end: 2027-03-31}

deals:                       # 3. announced deals
  enabled: true
  feeds: [https://www.secretflying.com/feed/, https://www.theflightdeal.com/feed/]
  watches:
    - {name: Europe from Seattle, origins: [Seattle, SEA, "West Coast", "US cities"],
       destinations: [europe], max_price: 60000}
```

Prices are in **cents**. Region names (`europe`, `japan`, `asia`, `hawaii`, `mexico`,
`caribbean`, `south america`, `oceania`, `africa`) expand to country and city words. Test a
headline with `flighttrack deals --test-match "Seattle to Paris for $412"`.

The shipped `config.yaml` is annotated section by section; the trip dates in it are placeholders
to edit.

### Filters: "record everything, filter at report time"

`max_stops` is `null` on every route, so nothing is discarded at fetch time. `carry_on_bags: 1`
and `exclude_basic_economy: true` stay on, not to discard options but so the recorded number
means one consistent thing; basic-economy fares mixed into the history corrupt every percentile
built on top.

### Coverage

The shipped config expands to ~150 queries against a 60/run cap, so detail coverage rotates over
~3 days by priority (1 = daily, 2 = every 3 days, 3 = weekly), oldest-unfetched first, so nothing
starves. Fixed trips are priority 1. Enabling the calendar sweep refreshes the whole grid daily
and spends the detail budget on the dates that look interesting.

---

## Commands

| Command | Does |
|---|---|
| `run` | The daily job: expand → sweep → fetch → deals → alert → health → html. Stages are isolated. |
| `serve` | `run` on a daily schedule **and** a web server for the report (`/`, `/status.json`, `POST /run`). One process for an always-on box. |
| `demo` | Seed synthetic history into `data/demo.db` and render `out/demo.html`. No network. |
| `compact` | Prune runner-up offers (>90d) and request logs (>180d), then VACUUM. Dry run unless `--yes`; `run` does it weekly. |
| `expand` | Materialise trips and patterns into `queries`. Idempotent, no network. |
| `fetch` | Fetch due queries through the source chain. `--dry-run` lists them. Skips itself during a cooldown. |
| `sweep` | Calendar RPC over every route/pattern window, then confirm the best candidates. Experimental. |
| `alert` | Evaluate fresh observations and notify. `--dry-run` prints. |
| `deals` | Poll feeds, match, notify. `--test-match TITLE` checks a headline offline. |
| `health` | Is the collector working? Exit 1 if not; `--notify` pushes (deduped daily). |
| `advise` | Book-or-wait verdicts with reasons, per date pair. |
| `report` | The grid. `--pattern`, `--dest`, `--label`, `--month`, `--sort price`, `--sparkline`, `--cheapest N`. |
| `status` | Run history, last week's request outcomes and latencies, health. **Look here first.** |
| `doctor` | Verify the data path. `--calendar`, `--dump PATH`, `--transport urllib`. |
| `calibrate` | Measure the rate limit (needs `--yes`). |
| `ingest` | Record prices from another tool, one JSON object per line. |
| `html` | Regenerate the static page. |

### The report

Every run rewrites `out/index.html` (and `flighttrack serve` serves it): trip cards with today's
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

ntfy by default — one HTTP POST, native phone push, self-hostable.

```bash
export NTFY_TOPIC="something-long-and-random"
```

Topics on the public ntfy.sh are readable by anyone who guesses the name; use a long random one
or self-host. `smtp` and `file` channels sit behind the same seam. Delivery failures are never
recorded as sent, so a broken channel does not let the dedup window swallow the retry.

---

## Scheduling

Three ways, in order of preference — details in `docs/running.md`:

1. **`docker compose up -d`** on a mini PC: `flighttrack serve` runs the job daily and serves the report.
2. **systemd** timer + service in `deploy/` (daily, jittered, hardened, catches up after sleep).
3. **launchd** agent in `deploy/` for a Mac, or one cron line:
   ```cron
   15 3 * * *  cd /srv/flighttrack && .venv/bin/flighttrack run >> log/run.log 2>&1
   ```

`run` exits non-zero when the collector aborted or health is critical, so cron mail surfaces it —
and `health` pushes to your phone as well.

---

## Data model

```
config.yaml ──► expand ──► queries ──┬──► fetch / sweep ──► observations (append-only)
                                     │                          │
deal feeds ──► deals ──► deal_posts  └──► alert ◄───────────────┤
                                            │                   │
                          fetch_attempts, run_log, state ──► health ──► notification
                                                                │
                                                        report / advise / HTML
```

`observations` is append-only and the database enforces it (`BEFORE UPDATE`/`DELETE` triggers
raise). Every historical percentile depends on nothing ever rewriting that table. Calendar-sweep
prices are stored with `is_best = 0` and `source = 'google_calendar'`: context, never the series.
Expired queries and dropped routes are deactivated, never deleted. Schema changes are additive
and applied automatically on connect. The one sanctioned exception to append-only is `compact`,
which removes runner-up offers and old request logs (never `is_best` rows) inside a transaction
that drops and immediately restores the delete trigger.

---

## Development

```bash
pip install -e '.[dev]'
pytest -q        # 195 tests, all offline; passes with or without primp/fast-flights
FLIGHTTRACK_BROWSER_TESTS=1 pytest tests/test_page.py   # also drives the page's JS in Chromium (needs playwright)
```

`FakeSource` and `FakeTransport` make every path testable without a request. The encoder test
against `fast-flights` skips if that extra is not installed.

## Terms of service

Automated querying of Google Flights is not sanctioned, which is precisely why the free API
options closed. The volume here is trivial and personal, but the constraint is real and is the
reason pacing is conservative by design, and why the deal feeds — the one fully sanctioned input
— carry the "tell me when something is announced" question.
