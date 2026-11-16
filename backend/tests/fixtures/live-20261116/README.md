# Live capture, 2026-11-16 (UTC)

Captured during the deal finder track's first milestone (D1) with `flighttrack doctor --calendar`
from a cloud sandbox (three requests in the session). Do not edit; capture new ones into a new
dated folder.

## `calendar_gated_sea_slc_3n.txt`

The whole `GetCalendarGraph` reply for SEA → SLC, 3 nights, from 2027-01-15, with the shipped
filters: HTTP 200, 363 bytes, no prices. The `wrb.fr` entry has no payload and carries Google's
error envelope instead: `[13, null, [["type.googleapis.com/travel.frontend.flights.ErrorResponse", …]]]`.

The results page for the same dates, seconds earlier, read normally (10 itineraries, cheapest
$394, the same as on 2026-11-14). A reply of the same size came back from the home address on
2026-11-15 and the next call, minutes later, returned 31 prices. Other clients report this RPC
answering so since 2026-08 unless the page's own script adds a token.

| What | Expected |
|---|---|
| `parse_calendar()` count | 0 |
| `looks_gated()` | `True` (Google declined; not a layout change) |

## Also checked, not saved

The full results page (2.3 MB) has five data blocks: `ds:0` echoes the request, `ds:1` holds the
itineraries, and `ds:2` to `ds:4` are the lists of countries, currencies and languages. None holds
Google's price insights ("prices are low / typical / high", a typical price range); the page's
37 uses of "typical" are CO₂ figures (`data-co2typical`). Price insights are not available from
the server-rendered page, so the deal finder (D3) works out its own usual prices.
