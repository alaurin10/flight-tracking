# flighttrack

A self-hosted daily price sensor for a fixed set of flight routes and date patterns.

Sites like [escape.flights](https://escape.flights) publish the **minimum** fare over a wide
window ("$443 SEA→ZRH, Sep–May, no Sunday departures"). This answers a different question:
**the full price surface across specific candidate dates** — what does every extended weekend
in January cost, sorted, and *is that a good price by this route's own history*.

The unique value over any website is **history**. A single query tells you a price. A table of
observations over time tells you whether that price is good.

**Not** a booking system (it reports; you book in a browser). **Not** a destination-discovery
engine (Escape Flights does that well). **Not** real-time — fares move over days, so daily
resolution is plenty.

---

## Status

| Milestone | State |
|---|---|
| 1. Spike — verify the data source | ⚠️ **partially complete — needs one command on your box** (see below) |
| 2. Calibrate the rate limit | ⬜ not started — `flighttrack calibrate` is ready to run |
| 3. Schema + expansion | ✅ done |
| 4. Fetcher with throttling + `run_log` | ✅ done |
| 5. Reporting (CLI + static HTML) | ✅ done |
| 6. Alerting phase 1 | ✅ done |
| 7. cron | ⬜ needs installing on the always-on box |
| 8. Alerting phase 2 (percentiles) | ✅ built, **gated off** until history justifies it |

### ⚠️ Two things you must do on the host before trusting the data

This was built in an environment whose egress policy **blocks `www.google.com`**, so the one
thing that cannot be verified offline is the live call. Everything else — query construction,
the `tfs` protobuf, deep links, normalization, scheduling, reporting, alerting — is verified by
97 offline tests.

**1. Run the milestone-1 gate.**

```bash
flighttrack doctor
```

This makes one real request and prints the raw price value beside the normalized cents. **Check
those dollar amounts against what google.com/travel/flights shows for the same search.** If they
match, milestone 1 passes and price normalization is correct.

The open question it closes: `fast-flights` 3.1.0 types `Flights.price` as `int`, but whether
that int is **dollars or cents** is not documented. This code assumes **dollars** and multiplies
by 100. If `doctor` shows raw values that are already cents (e.g. `81100` for an $811 fare), flip
`assume_major_units` in `src/flighttrack/source.py`. Every observation stores the original value
in its `raw` JSON, so a wrong guess is **re-derivable from stored data** — you would not have to
re-collect anything.

**2. Calibrate the rate limit.** It is the binding constraint on the whole system and its ceiling
is undocumented — it is Google's per-IP behaviour. Measure it; don't assume it.

```bash
flighttrack calibrate --count 20 --interval 6 --out calibration-6s.csv --yes
flighttrack calibrate --count 50 --interval 4 --out calibration-4s.csv --yes
```

Record the numbers here when you have them:

| Interval | Requests before first failure | Notes |
|---|---|---|
| 10s | _not yet measured_ | |
| 6s | _not yet measured_ | |
| 4s | _not yet measured_ | |
| 2s | _not yet measured_ | |

Until then the shipped pacing is the deliberately conservative starting posture: serial, 4–8s
jittered, 60 queries/run max, abort after 2 consecutive failures.

---

## Install

Python 3.10+ required (`fast-flights` needs it).

```bash
git clone https://github.com/alaurin10/flight-tracking.git
cd flight-tracking
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

```bash
export NTFY_TOPIC="pick-something-long-and-random"   # see "Notifications"
flighttrack expand          # build the date grid — no network
flighttrack doctor          # the milestone-1 gate — one real request
flighttrack fetch           # collect prices, then regenerate the HTML page
flighttrack report --label Tokyo --pattern extended_weekend_thu --month 2027-01
```

---

## Configuration

Everything lives in `config.yaml`. There is **deliberately no settings GUI** — config changes are
rare (a route every few months, a target price adjusted after a season of data), and a YAML file
is diffable, version-controllable and editable over SSH. A settings UI is weeks of work to save a
handful of text edits per year. Resist it until the system has run for six months and the friction
is demonstrated rather than anticipated.

### Routes as configured

| Dest | Label | Target | Priority | Why |
|---|---|---|---|---|
| HND | Tokyo | $650 | 1 (daily) | Haneda — closer in, usually pricier |
| NRT | Tokyo | $600 | 2 (every 3d) | Narita — skews cheaper (ZIPAIR et al.) |
| SLC | Salt Lake City | $140 | 1 (daily) | Short-haul; Delta hub, Alaska competes |

HND and NRT are tracked as **separate routes sharing the label "Tokyo"**, because their fares
genuinely differ. `report --label Tokyo` shows both together with a `DEST` column;
`report --dest HND` isolates one.

**The target prices are guesses, not measurements.** They exist only so phase-1 alerting has
something to fire on. Revise them once you have seen a season of real data.

### Patterns

All four expand against every route:

| Pattern | Shape |
|---|---|
| `extended_weekend_thu` | Thu → Mon (4 nights) |
| `extended_weekend_fri` | Fri → Mon (3 nights) |
| `full_week` | Sat → Sat (7 nights) |
| `long_haul_10` | Sat → Tue (10 nights) |

### Filters: "record everything, filter at report time"

`max_stops` is `null` on every route, so nothing is discarded at fetch time and you can slice by
stops later from stored data.

Two settings are kept on anyway, and they are **not** about discarding options —
they make the recorded number mean one consistent thing:

- `exclude_basic_economy: true`
- `carry_on_bags: 1`

Without them the history mixes basic-economy fares that are not like-for-like, which quietly
corrupts every percentile and every "is this cheap" judgement built on top. Both are one-line
changes if you would rather record the true headline minimum.

---

## The coverage trade-off (read this before changing the horizon)

The current config expands to **378 active queries** — 3 routes × 4 patterns × ~31 matching
weekdays inside the 21–240 day horizon. The safety cap is **60 queries per run**.

378 does not fit in 60, so the fetcher **rotates** instead:

1. A query is due only once its route-priority interval has elapsed (priority 1 = daily,
   2 = every 3 days, 3 = weekly).
2. Due queries are ordered by priority, then by how long they have gone unfetched, then by how
   soon they depart.
3. The run takes the first 60.

So **full coverage cycles about every 7 days rather than daily**, and no query can starve — one
skipped today sorts higher tomorrow. `flighttrack expand` prints this arithmetic every time.

Three ways to tighten it, in order of preference:

1. **Narrow the horizon.** `max_days_ahead: 120` roughly halves the grid. Fares 8 months out move
   slowly and are the least actionable rows in the table.
2. **Tier by priority** rather than scaling uniformly — demote `long_haul_10` routes to priority 3.
3. **Raise the cap** once `calibrate` tells you what is actually safe.

Resist growing query volume without evidence it helps. A dense grid of near-identical Tuesdays
mostly produces noise you then have to filter back out.

---

## Commands

| Command | Does |
|---|---|
| `flighttrack expand` | Materialise date patterns into `queries`. Idempotent. Run after any config change. No network. |
| `flighttrack fetch` | Fetch due queries, append observations, regenerate the HTML page. `--dry-run` shows what it would do. |
| `flighttrack alert` | Evaluate fresh observations and notify. `--dry-run` prints instead of sending. |
| `flighttrack report` | The date-grid table and other slices. No network. |
| `flighttrack html` | Regenerate the static page on demand. |
| `flighttrack status` | Run history and health. **Look here first when something seems wrong.** |
| `flighttrack doctor` | Verify the data source and confirm price units. |
| `flighttrack calibrate` | Measure the rate limit (needs `--yes`; makes real requests). |

### Reporting

```bash
# the original complaint, answered
flighttrack report --label Tokyo --pattern extended_weekend_thu --month 2027-01

# price history per date pair
flighttrack report --dest HND --sparkline

# the closest thing to "where should I go"
flighttrack report --cheapest 10
```

```
SEA → Tokyo · extended weekends · January 2027        (as of 2026-09-16)

  DEST  DEPART      RETURN        CURRENT   30d LOW   vs LOW  AIRLINE     STOPS
  ──────────────────────────────────────────────────────────────────────────────
  HND   Thu Jan 07  Mon Jan 11       $763      $759      +1%  Alaska          0
  NRT   Thu Jan 07  Mon Jan 11       $910      $767     +19%  Alaska          0
  HND   Thu Jan 14  Mon Jan 18     $1,024      $925     +11%  Alaska          0
  NRT   Thu Jan 14  Mon Jan 18       $871      $808      +8%  Alaska          0
```

### The static HTML page

Every `fetch` rewrites a single static file (`out/index.html` by default) with the current grid,
30-day lows, and sparklines. No framework, no web app, no API — just a file to serve from
whatever already runs on the box.

This exists because of *when* you actually want this data: idly, from a phone, wondering about
January. A CLI on a home server is unreachable in exactly that moment.

**Every departure date is a deep link** into the exact Google Flights search, built from the same
`tfs` protobuf parameter used to fetch it. An alert you have to re-enter by hand is an alert you
ignore.

---

## Alerting

Two-phase by design. A fixed price threshold ages badly — set $650 for Tokyo and it may never
fire, and a system that never fires is one you forget you installed. But a threshold is the only
thing available before history exists.

### Phase 1 (now) — weeks 0–4

Fires when either:
- the price is at or below the route's `target_price`, or
- it is a genuine **all-time low** for that date pair, requiring ≥5 prior observations so the
  first sighting isn't trivially a record.

> **Phase 1 is expected to be QUIET. That is the system accumulating, not failing.**
> Please do not "fix" the silence by loosening thresholds in week one — you will spend
> phase 2 fighting the noise you added. Check `flighttrack status`: if runs are succeeding and
> observations are growing, it is working.

### Phase 2 (later) — once ≥30 days of history exists

Fires when a price is at or below the 20th percentile of that **route's** trailing-60-day
observations — computed across all date pairs on the route, because route-level baselines are far
more robust than per-date-pair ones, which have too few samples to mean anything.

It is written and tested but **gated off**. Turn it on when the history justifies it:

```yaml
alerts:
  percentile_phase2_enabled: true
```

Check readiness with `flighttrack status` (see `history span`).

### Deduplication

Without this the system becomes noise and gets muted:
- no re-alert on the same date pair within **7 days** unless it dropped a further **≥10%**;
- at most **3 alerts per run** — beyond that, one digest instead of three pushes;
- only observations from the last **48 hours** are considered, so a stale cheap fare can't re-fire
  forever.

### Notifications

ntfy, over one HTTP POST — native phone push, no SMTP credentials, self-hostable.

```bash
export NTFY_TOPIC="something-long-and-random"
```

> **Topics on the public ntfy.sh are readable by anyone who guesses the name.** Use a long random
> topic, or self-host and set `notify.ntfy.server`. The topic is read from the environment so it
> never lands in git.

`smtp` and `file` channels ship behind the same `notify()` seam — switch with one YAML line.
Delivery failures are never recorded as sent, so a broken channel doesn't let the dedup window
swallow the retry.

---

## cron

```cron
# Fetch off-peak, then alert. Separate processes so they fail independently:
# a broken notification channel must never stop history from accumulating.
15 3 * * *  cd /srv/flighttrack && NTFY_TOPIC=xxx .venv/bin/flighttrack fetch  >> log/fetch.log 2>&1
45 3 * * *  cd /srv/flighttrack && NTFY_TOPIC=xxx .venv/bin/flighttrack alert  >> log/alert.log 2>&1

# Re-expand weekly to roll the horizon forward and retire past departures.
0  2 * * 1  cd /srv/flighttrack && .venv/bin/flighttrack expand >> log/expand.log 2>&1

# Weekly heartbeat. Silent failure is the worst outcome for this system —
# you stop hearing about deals and assume there are none.
0  9 * * 1  cd /srv/flighttrack && .venv/bin/flighttrack status | tail -20 >> log/status.log 2>&1
```

`fetch` exits non-zero when a run aborts on consecutive failures, so cron mail surfaces it.
**Never re-run immediately after an abort** — retrying into a rate limit extends the lockout on a
sliding-window limiter.

---

## Data model

```
config.yaml ──► expand ──► queries ──┬──► fetch ──► observations (append-only)
                                     │                     │
                                     └──► alert ◄──────────┤
                                            │              │
                                            ▼              ▼
                                     notification    report / HTML
```

Five tables: `routes`, `queries`, `observations`, `alerts_sent`, `run_log`.

**`observations` is append-only, and the database enforces it** — `BEFORE UPDATE` and
`BEFORE DELETE` triggers raise. Every historical percentile and all-time low depends on nothing
ever rewriting that table, so it is a property of the schema rather than a rule someone remembers.
Correcting bad data means recording a new observation.

Multiple rows are stored per fetch (the cheapest 5 by default) with `is_best` flagging the
minimum. Storing only the minimum throws away what you need to answer "was the cheap one a 2-stop
redeye?"

Expired queries and dropped routes are **deactivated, never deleted** — their observations are the
historical baseline.

`run_log` is the health monitor. A weekly glance, or an alert on `blocked=1`, is what prevents the
worst failure mode: cron dies, alerts stop, and you conclude there are no deals.

---

## Upgrading fast-flights

**Pinned to `3.1.0` on purpose. Never upgrade automatically.** A version that changes the response
shape will corrupt the observation history silently.

Before upgrading: read the changelog, run `flighttrack doctor`, and confirm prices still normalize
to the same units. The library's roadmap mentions using Google's internal `GetShoppingResults`
endpoint with the note *"Get ready to get banned"* — **do not upgrade into that.**

Known issues with 3.1.0:
- It imports `typing_extensions` without declaring it as a dependency. `pyproject.toml` pins it
  explicitly so a clean install works.
- Single maintainer who says plainly he is busy. It will break eventually and the fix may be a
  community PR. `run_log` surfaces it fast; the Bright Data integration is a paid escape hatch.

When it does break, `src/flighttrack/source.py` is the only file that needs attention — the rest of
the system speaks `Offer`, and `FakeSource` keeps the test suite working offline.

---

## Development

```bash
pip install -e '.[dev]'
pytest -q        # 97 tests, all offline — no test touches the network
```

`FakeSource` makes the whole pipeline testable without a single request.

---

## Terms of service

Automated querying of Google Flights is not sanctioned, which is precisely why the free API
options closed (Amadeus' independent tier and sandbox shut down 2026-07-17; Kiwi Tequila closed to
new independent developers). The volume here is trivial and personal, but the constraint is real
and is the reason pacing is conservative **by design rather than as an afterthought**.
