# Day to day

You do not operate this system; you glance at it. Two surfaces:

## 1. The phone push

You get a notification when something is worth your attention, and only then:

- **a price alert** — a tracked date hit your target, set an all-time low, or fell
  into the cheapest slice of its route's history. The notification carries the
  price, the dates, the airline, *why* it fired, and a link that opens the exact
  Google Flights search. Tap, check, book.
- **a deal post** — a feed you follow announced a deal matching one of your
  watches ("Seattle to Paris $438"). Tap for the article.
- **a health warning** — the collector has not succeeded in 36 hours, every
  request is failing, or the page layout changed. This is the one you should not
  ignore: silence otherwise means "no deals", and you need to know when it
  actually means "not looking".

Alerts are rate-limited by design (no repeat on the same dates within a week
unless it drops another 10%; at most three pushes per run, then a digest). If it
is quiet for the first weeks, that is history accumulating, not a fault.

## 2. The report

Open it when you are idly wondering. It is one page, top to bottom in the order
you would ask the questions:

**Your trips.** One card per fixed trip: today's price, how it moved this week,
a verdict (**BOOK · LEAN BOOK · HOLD · WAIT**) with the reasons in plain words,
your target, the all-time low, and the price history with your target drawn on
it. Hover or arrow-key the chart for exact values; the same numbers are in the
collapsible table underneath.

**Best in each grid.** The cheapest date of every pattern right now, so "which
weekend" is answered before you scroll.

**Date grids.** Every tracked departure as a tile, shaded by price (the scale is
under each grid), the cheapest outlined, ★ where it is under target, `low` where
it sits at its 30-day low. Tap a tile to open that search. The **Table** button
gives the same data sorted by price.

**Announced deals.** What your feeds posted that matched your watches.

**Collector.** Whether the tracker itself is working: last run, request outcomes
per day, and any problem the watchdog found.

The chips at the top filter everything below to one destination.

## Changing what is tracked

Edit `config.yaml`; the next run picks it up (or `flighttrack expand` now).

| You want to… | Do |
|---|---|
| Track a trip with fixed dates | add to `trips:` with its own `target_price` |
| Track "any long weekend in a window" | add a pattern with `depart_dow`, `nights`, `routes`, `window` |
| Hear about announced deals to a region | add a `deals.watches` entry with `origins`, `destinations`, `max_price` |
| Check a headline against your watches | `flighttrack deals --test-match "Seattle to Rome from $489"` |
| Add a destination | add to `routes:` (label, target, priority) |
| Stop tracking something | delete it; history is kept, the rows are deactivated |

## When you have decided

Book in the browser from the deep link; the tracker never books anything. Then
remove the trip (or leave it: watching the price after you bought is a known
form of self-harm, and the card will keep updating).

## Questions the terminal answers faster

```bash
flighttrack advise --pattern japan                 # book or wait, with reasons
flighttrack report --pattern ski_weekend --sort price
flighttrack report --dest HND --sparkline          # price history per date
flighttrack status                                 # is it working?
flighttrack demo                                   # render the report with synthetic data
```
