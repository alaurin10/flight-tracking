# Day to day

You do not operate this system; you glance at it. Two surfaces:

## 1. The alert

You get a notification when something is worth your attention, and only then. It
lands in `out/digest.md` with the shipped config, or in your inbox with
`notify.channel: smtp`; the web app will deliver it as a browser push.

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
unless it drops another 10%; at most three alerts per run, then a digest). If it
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

What is watched lives in the database. Change it on the app's **Manage** page: sign
in with the owner password and pick one of four ways to watch.

| Way | You give | What happens |
|---|---|---|
| **A trip** | where to, the day you leave and come back (or one way), optionally an alert price | its price is checked a few times a day until it departs; the verdict says book or wait |
| **Flexible dates** | where to (one or more), how long (weekend, long weekend, a week, or custom days and nights), when (months, any time, or a date range), optionally an alert price | every matching date is priced and the cheapest is shown; the form says how many dates that is before you save |
| **A region** | a region (Europe, Japan, Hawaii…) or places you name, optionally months or a season, optionally a maximum price | posts on your deal pages from home to the region, in those months, under the price, alert you. Fares come from deal pages, not Google |
| **A deal page** | a deal site's page you follow, or its feed | it is read every few hours; every new post shows on **Deals**, and posts that match a region, trip or flexible search alert you |

Anything else (where you fly from, stops, a name, notes, the words a deal must use for
home) is under **More options**; names are made up when you leave them empty. Tap
something in the list to change it; its **⋯** menu checks it now, pauses, resumes,
marks a trip booked or deletes it (prices already collected are kept).

A deal page with no feed (escape.flights' category pages, for example) is read from the
page itself: each heading that links to a post is a post. The first read only records
what is there, so old posts don't alert; new ones do. A post that names travel months
("[Nov-May, Aug]", "summer") only matches a region or trip for those months; one that
names none matches any.

The same can be done from a terminal with a YAML file (see
`config/seed.example.yaml`), handy for loading many at once or keeping a copy:

```bash
flighttrack export-config --out my-trips.yaml   # what is tracked now
# edit my-trips.yaml
flighttrack import-config my-trips.yaml         # creates and updates by name; never deletes
```

In that file:

| You want to… | Do |
|---|---|
| Track a trip with fixed dates | add to `trips:` with its own `target_price` |
| Track "any long weekend in a window" | add a pattern with `depart_dow`, `nights`, `routes`, `window`; it becomes a flexible search |
| Hear about announced deals to a region | add a `deals.watches` entry with `origins`, `destinations`, `max_price` |
| Check a headline against your watches | `flighttrack deals --test-match "Seattle to Rome from $489"` |
| Pause, resume or archive something | set `status: paused` (or `active`, `archived`; trips also `booked`) and import again |
| Stop tracking something | pause or archive it; its history is kept and its dates stop being fetched |

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
