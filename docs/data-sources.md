# Data sources: are the query tools enough, and what did we build instead?

The brief: alert on low airfares for three kinds of question, with **constant, reliable
checks**. This document is the honest inventory of every way to get a fare into this
system, what each one is good for, how it fails, and the layered design that came out of it.

## TL;DR

| Layer | Source | Role | Status |
|---|---|---|---|
| 1 | **Our own Google Flights client** (`gflights/`) | primary, no third-party scraper, no key | built; encoder proven byte-identical to a working library; live call verified on your host by `doctor` |
| 2 | `fast-flights` library | independent second parser of the same page | optional fallback, pinned |
| 3 | **Calendar-graph RPC** (`sweep`) | one call prices ~2 months of dates; makes daily whole-grid coverage cheap | experimental; verify with `doctor --calendar` before enabling |
| 4 | **SerpApi** `google_flights` | paid JSON API on someone else's IP | built; active only when a key is set |
| 5 | **RSS deal feeds** (`deals`) | announced deals to a region | built; the only fully sanctioned, stable input |
| 6 | `ingest` | anything else (a browser session, the Expedia connector in Claude, a friend's screenshot) | built |

**The single scraper the first version relied on was not enough.** It was one parser, one
transport, one maintainer, reading only Google's "best" list (which systematically overstates
the minimum fare), with no way to tell a rate limit from a layout change from a network blip.
Everything below exists to remove one of those single points of failure.

---

## The options, one by one

### A. Google Flights, the results page (what `fast-flights` scrapes)

**How it works.** The page at `google.com/travel/flights/search?tfs=…` is server-rendered and
embeds a JSON blob (`AF_initDataCallback({key:'ds:1', data:[…]})`) containing every itinerary
Google would show, with prices. The `tfs` parameter is a small protobuf naming the legs, cabin,
passengers and filters. No JavaScript execution is needed.

**Why it is the primary.** It is the most complete, most current fare data there is, it is
free, it covers every airline, and the request is one GET. Every price-tracking product you
have heard of is downstream of it one way or another.

**How it fails.**
1. *Rate limiting.* Undocumented, per-IP. Symptoms: HTTP 429, a redirect to `/sorry/`, or a
   captcha page. The number is unknown until you measure it (`flighttrack calibrate`).
2. *Layout changes.* The blob's positional indices shift, or the script element is renamed.
   A parser that trusts one index chain dies entirely.
3. *Consent interstitial* for EU IPs.
4. *Bot heuristics on the TLS handshake.* A Python TLS fingerprint is not a Chrome one.

**What `fast-flights` 3.1.0 gets wrong for our purpose** (read from its source, not its README):
- it reads only `payload[3][0]` ("top departing flights") and ignores `payload[2][0]`
  ("other departing flights"), which is usually where the cheapest itinerary is;
- `parser.css_first("script.ds:1").text()` raises `AttributeError` on any page without that
  element, so a captcha, a consent page and a redesign all surface as the same exception;
- for round trips it lists outbound segments priced at the round-trip total, which the
  previous version of *this* project mis-read as "segments minus two legs", under-counting stops
  by one on every connecting itinerary (fixed);
- one maintainer, who says plainly he is busy; a compiled dependency (`primp`) and `protobuf`.

**What we built instead — `flighttrack/gflights/`:**
- `tfs.py` — a 150-line hand-rolled protobuf encoder. No `protobuf` dependency. The test suite
  asserts it is **byte-identical** to `fast-flights` across 500+ option combinations, and a `tfs`
  copied from a live Google URL decodes with the same field layout.
- `transport.py` — `primp` (browser TLS fingerprint) when installed, stdlib `urllib` otherwise.
  Both honour `HTTPS_PROXY`. A consent-accepted cookie is sent up front.
- `page.py` — classifies every response as one of **results / no-results / consent / blocked /
  error-status / layout-unknown**. The data blob is looked for first (by element class, then by
  callback name), so a stray word like "recaptcha" in a script can never trigger a false block.
- `results.py` — reads **both** itinerary lists, de-duplicates, tolerates a shifted index by
  degrading one field instead of failing the fetch, keeps the raw row.
- Unreadable pages are saved to `data/failures/` (rotating, last 20). When it breaks, the
  evidence is on disk, not in a log line.

**What is not verified.** The environment this was built in cannot reach Google. The encoder
and parser are proven against fixtures and against a library that works live; the live request
itself is proven the first time you run `flighttrack doctor` on your box.

### B. Google Flights, the calendar-graph RPC (`sweep`)

**How it works.** The "price graph" in the Google Flights UI is fed by an internal
`batchexecute` endpoint (`GetCalendarGraph`) that returns the lowest price for every departure
date in a window for a fixed trip length. One POST ≈ 60 dates.

**Why it matters.** Question 2 ("any Fri→Mon, January through March") is 25 date pairs. With
the results page that is 25 requests, and the full grid of 150+ queries takes days to rotate
through at a safe pace. With this endpoint the entire grid is refreshed **daily in about a dozen
calls**, and only the interesting dates get a confirming results-page fetch
(**sweep → confirm → alert**: nothing alerts on a calendar number alone).

**Risk.** It is an undocumented internal RPC; the request layout was reconstructed from
open-source clients and browser traffic, not verified live from here. So:
- it is **off by default** (`calendar.enabled: false`);
- `flighttrack doctor --calendar` makes one call and prints what came back;
- the parser is shape-agnostic — it walks the response for `[date, …, price]` tuples rather than
  trusting indices — and `--dump` saves the raw response for a human;
- its numbers are stored as *context* (`source='google_calendar'`, never `is_best`), so they can
  never become a "current price" or an alert without confirmation.

### C. Official flight-search APIs

| API | Verdict |
|---|---|
| **Amadeus Self-Service** | Was the obvious choice. The previous version of this project recorded that its independent tier and sandbox were shut down on 2026-07-17; enterprise contracts only. Not viable for a personal tracker. |
| **Duffel** | Real bookable fares, good API. Production access requires a verified business account; the test environment returns synthetic data. Not for individuals. |
| **Kiwi Tequila** | Closed to new independent developers. |
| **Skyscanner / Kayak / Expedia partner APIs** | Affiliate-partner programmes with traffic and revenue requirements. |
| **Travelpayouts / Aviasales data API** | Free cached-price endpoints (cheapest by month, price calendar) with an affiliate account. Genuinely useful for coarse "cheapest month" questions; coverage skews to its own OTA network and prices are cached, not live. A reasonable *future* coarse source; not built, because the calendar RPC answers the same question from the primary dataset. |
| **SerpApi / SearchAPI** (`google_flights` engine) | Paid scraping-as-a-service that returns the Google Flights page as JSON. ~$50/month at daily volume. **Built** as the `serpapi` source: the escape hatch when your IP is blocked or the page changes faster than the parser is fixed. Same typed errors, same chain. Skipped silently unless `SERPAPI_KEY` is set. |

### D. The Expedia connector in Claude

The `search_flights` tool available in a Claude session returns real bookable itineraries with
taxes and fees. It is a good *second opinion* and it is what the first round of this project
leaned on. It is not a data source for a cron job: it is only callable from inside a Claude
conversation, cannot be scheduled from your box, and gives no price history. The `ingest`
command exists so a price you find that way (or anywhere else) still lands in the same table,
as an observation with `source='expedia'`, and joins the history and the alerts.

### E. Deal feeds (RSS)

Secret Flying, The Flight Deal, Fly4free and similar sites publish RSS feeds *on purpose*. This
is the one input that is fully sanctioned, needs no key, has no rate limit worth mentioning and
has not changed shape in a decade. It answers question 3 directly: a watch is a list of origin
words, destination words (or a named region), and a maximum price, matched against headlines.
Going (formerly Scott's Cheap Flights) is email-only and paid; not included.

### F. Airline websites, Kayak, Skyscanner pages

Heavily bot-protected, JavaScript-rendered, per-airline. Not worth it while Google aggregates
them all.

---

## The reliability design

Robustness here is not "retry harder". It is:

1. **Typed failures** (`NoResults`, `BlockedError`, `NetworkError`, `LayoutError`) and a
   different response to each — move on / stop and cool off / retry once / try the next parser.
2. **A source chain** with failover only where it helps: a layout failure falls through to the
   next parser; a block stops the chain, because every source shares your IP.
3. **Cooldown as state, not as advice.** A blocked run writes `cooldown_until` into the database
   and escalates (1h → 4h → 12h → 24h). The next cron run skips itself. Nobody has to remember
   not to re-run.
4. **Every request logged** (`fetch_attempts`: outcome, latency, source). Reliability is
   measured continuously, not once by `calibrate`.
5. **Failure artifacts** on disk for the pages we could not read.
6. **A watchdog** (`health`) that pushes a notification when nothing has succeeded for 36h, when
   every request is failing, when the page appears to have changed, or when a feed is dead —
   deduplicated so it nags once a day, not once a run. *The absence of alerts is never mistaken
   for the absence of deals.*
7. **Offline-provable parts are proven offline.** 187 tests, none touch the network, and the
   suite passes with and without the optional dependencies.

## What to run on the host

```bash
flighttrack doctor                # one real request; confirms transport, parser, price units
flighttrack doctor --calendar     # one calendar RPC; enable `calendar` only if it prints sane prices
flighttrack calibrate --count 20 --interval 6 --yes   # measure the rate limit before raising the cap
flighttrack run --dry-run         # the whole daily job, alerts printed instead of sent
```
