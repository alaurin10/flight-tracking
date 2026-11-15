# Live captures, 2026-11-14

Real Google Flights responses, captured once from a cloud sandbox. They exist so the parsers
are tested against what Google actually sends, not only against hand-built payloads.
Milestone M1 in `docs/PLAN.md` adds the tests that read them.

Do not edit these files. To refresh them, capture new ones into a new dated folder.

## `results_sea_slc_rt.html`

The results page for SEA → SLC, depart 2027-01-15, return 2027-01-18, 1 adult, economy,
`carry_on_bags=1`, `exclude_basic_economy=true` (the shipped `search:` settings). The original
page was 2.3 MB; only the `<script class="ds:1">` data block is kept (33 KB).

The same search was rendered in Chromium at capture time and every row matched.

| What | Expected |
|---|---|
| `classify()` | `PageKind.RESULTS` |
| `itineraries()` count | 12 |
| Prices, ascending | 394, 447, 477, 477, 482, 527, 533, 597, 627, 627, 627, 627 |
| Cheapest | $394, Frontier, 1 stop via DEN, flights F9 1916 + F9 4251 |
| Total trip time of the cheapest (`raw[0][9]`) | 532 min (its two segments are 169 + 98 min) |
| `payload[2][0]` | 5 rows, shown on the page as **Top departing flights** (Delta 447, 477, 477, 527; Alaska 533) |
| `payload[3][0]` | 7 rows, shown as **Other departing flights** (Frontier 394, Southwest 482, Delta 597, Alaska 627 ×4) |
| `payload[25]` | `[[null, 394]]`: the page's own "cheapest" figure, equal to the lowest parsed price |
| `payload[7][0]` | `[[null, 304], [null, 1595]]`: the price range before the bag and fare filters |

Companion measurements for the same dates, taken minutes apart (not stored as files):

| Filters | Cheapest |
|---|---|
| none | $304 |
| carry-on bag only | $347 |
| exclude basic economy only | $394 |
| both | $394 |

## `calendar_sea_slc_3n.txt`

The raw `GetCalendarGraph` response for SEA → SLC, 3 nights, departures 2027-01-15 to
2027-02-14, sent **without** bag or fare filters (the request `build_body()` made that day).

| What | Expected |
|---|---|
| `parse_calendar()` count | 31 |
| First | 2027-01-15 → 2027-01-18, $304 (equals the unfiltered results page above) |
| Last | 2027-02-14 → 2027-02-17, $262 |
| Lowest | 2027-01-24 → 2027-01-27, $200 |

With filter-array slot 10 set to `[1, 0]` (one carry-on), the same request returned $347 for
2027-01-15, equal to the carry-on-only results page. That response was not saved.
