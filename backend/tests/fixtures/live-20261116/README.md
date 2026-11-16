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

## `explore_sea_anywhere_3n.txt`

The `GetExploreDestinations` reply for SEA → Anywhere, round trip, 3 nights from 2027-01-15,
with the shipped bag filter (D2's first live check, through `flighttrack doctor --explore
anywhere`). HTTP 200, 1,411,850 bytes. Trimmed to 28 KB: the map's country outlines (`[3][3]`
of the first entry, 1.42 MB) and each destination's two image links (`record[3]`, `record[7]`)
are set to `null`; nothing else was changed, and the batchexecute framing is as received.

| What | Expected |
|---|---|
| `parse_explore()` places | 92 (origin "Seattle") |
| With a fare | 6: Dubai DXB $548, Mumbai BOM $639, Lima LIM $823, Athens ATH $839, Buenos Aires EZE $954, Johannesburg JNB $1,374 |
| Los Angeles | listed, no fare |
| `looks_gated()` | `False` |

The 67 fare records without a price read `["multi", "", 0, 0, …]`: Google had no fare for those
places under these filters at that moment. A second call minutes later (4 nights, not saved)
priced a different 8 of the same 92 (Chicago MDW $205, Nashville BNA $244, …), so each call
returns the fares Google already has, and a board fills in over departures and repeated calls.
The outside capture this layout comes from (London → Southern Europe, fli PR 226) priced 46 of
58. Seven requests to Google were made in the session that captured this and the calendar file.
