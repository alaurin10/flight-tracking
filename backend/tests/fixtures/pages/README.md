# Deal pages

`escape-flights-seattle.html`: the first three posts of
`https://escape.flights/category/flights-from-seattle/`, fetched on 2026-11-15, with a
made-up header, sidebar and pagination around them (links that are not posts, and a
script holding post-like markup) to show what the page reader skips. The site has no
RSS feed: its `/feed/` address answers with a web page.

Expected from `deals.parse_page`: 3 posts, in page order.

| Title starts | Price | Months named |
|---|---|---|
| Cheap Flights: Seattle to London $583-$675 | $583 | Nov–May and Aug |
| Nonstop Flights: Seattle to/from San Francisco, Los Angeles, Phoenix, or San Diego $97-$137 | $97 | Dec |
| Nonstop Flights: Seattle to Salt Lake City $134-$157 | $134 | Dec |
