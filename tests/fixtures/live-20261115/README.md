# Live capture, 2026-11-15 (UTC)

Captured during milestone M1's live check (`flighttrack doctor`, from a cloud sandbox). Do not
edit; capture new ones into a new dated folder.

## `results_error13_sea_slc_rt.html`

The results page for SEA → SLC, depart 2027-01-13, return 2027-01-16, with the shipped `search:`
settings. HTTP 200, 1.9 MB; only the `<script class="ds:1">` block is kept.

Instead of itineraries, `ds:1` holds `[13, null, [["type.googleapis.com/travel.frontend.flights.ErrorResponse", …]]]`
with `errorHasStatus: true`. 13 is the gRPC code for an internal error.

This was **not** a lack of inventory and **not** a block:

| Check, minutes apart | Result |
|---|---|
| Calendar call, same route, 3 nights from 2027-01-13 | 31 prices; 2027-01-13 → 2027-01-16 at $277 |
| Results page for 2027-01-15 → 2027-01-18, 30 seconds later | 10 itineraries, cheapest $394 (the same as on 2026-11-14) |

| What | Today's behaviour |
|---|---|
| `classify()` | `PageKind.NO_RESULTS`, "Google reported no itineraries (errorHasStatus)" |

So a Google-side error is recorded as "no itineraries": `fetch.run` marks the query fetched and
resets its failure counter, and that date pair goes without a price until it is next due. The
plan's problem table (row 13) tracks the fix; no test pins today's behaviour.
