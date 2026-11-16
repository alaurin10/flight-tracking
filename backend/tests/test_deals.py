"""Deal feeds: parsing, matching, polling, alerting."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from helpers import FakeTransport, make_config, make_db

from flighttrack import deals
from flighttrack.config import Deals, DealWatch

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Deals</title>
<item><title>Seattle to Paris, France for only $412 roundtrip</title><link>https://d/1</link>
<guid>d1</guid><pubDate>Wed, 16 Sep 2026 10:00:00 +0000</pubDate>
<description>&lt;p&gt;Nonstop on Air France&lt;/p&gt;</description></item>
<item><title>Many US cities to Tokyo, Japan from $598 roundtrip</title><link>https://d/2</link><guid>d2</guid>
<pubDate>Wed, 16 Sep 2026 11:00:00 +0000</pubDate></item>
<item><title>Chicago to Lisbon from $455</title><link>https://d/3</link><guid>d3</guid>
<pubDate>Wed, 16 Sep 2026 12:00:00 +0000</pubDate></item>
<item><title>Seattle to London for $1,240 in business class</title><link>https://d/4</link><guid>d4</guid>
<pubDate>Mon, 01 Jan 2001 12:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom deals</title>
<entry><title>West Coast to Rome from $489</title><id>a1</id><link href="https://a/1"/>
<updated>2026-09-16T09:00:00Z</updated><summary>via Norse</summary></entry>
</feed>"""

# The fixtures above are dated 16 Sep 2026; poll() drops posts older than its lookback.
NOW = datetime(2026, 9, 17, tzinfo=UTC)


def _cfg(tmp_path, **over):
    watches = (
        DealWatch(
            "Europe",
            origins=("Seattle", "SEA", "West Coast", "US cities"),
            destinations=("europe",),
            max_price=60000,
        ),
        DealWatch("Japan any price", origins=("Seattle", "US cities"), destinations=("japan",)),
    )
    cfg = make_config()
    return replace(
        cfg,
        deals=Deals(enabled=True, feeds=("https://rss/feed", "https://atom/feed"), **over),
        deal_watches=list(watches),
    )


def test_parse_rss_and_atom():
    items = deals.parse_feed(RSS, "https://rss/feed")
    assert [i.guid for i in items] == ["d1", "d2", "d3", "d4"]
    assert (
        items[0].published == "2026-09-16T10:00:00Z" and items[0].summary == "Nonstop on Air France"
    )
    a = deals.parse_feed(ATOM, "https://atom/feed")
    assert (
        a[0].link == "https://a/1"
        and a[0].guid == "a1"
        and a[0].published == "2026-09-16T09:00:00Z"
    )


def test_price_and_region_matching(tmp_path):
    cfg = _cfg(tmp_path)
    items = {i.guid: i for i in deals.parse_feed(RSS, "f")}
    assert deals.price_from_text(items["d4"].title) == 124000
    assert deals.first_match(items["d1"], cfg.deal_watches).name == "Europe"
    assert deals.first_match(items["d2"], cfg.deal_watches).name == "Japan any price"
    assert deals.first_match(items["d3"], cfg.deal_watches) is None  # Chicago
    assert deals.first_match(items["d4"], cfg.deal_watches) is None  # over max price
    rome = deals.parse_feed(ATOM, "f")[0]
    assert deals.first_match(rome, cfg.deal_watches).name == "Europe"  # region word + West Coast


def test_word_boundaries_avoid_substring_hits():
    item = deals.FeedItem("g", "Houston to Seaside Oregon for $99", None, None, "", "f")
    assert not deals.matches(item, DealWatch("w", ("SEA",), ()))
    assert deals.matches(item, DealWatch("w", ("Houston",), ("Oregon",)))


def test_poll_stores_matches_and_isolates_feed_failures(tmp_path):
    cfg = _cfg(tmp_path)
    conn, cfg = make_db(tmp_path, cfg)
    t = FakeTransport([(200, None, RSS), (500, None, "boom")])
    stats = deals.poll(conn, cfg, transport=t, now=NOW, verbose=False, sleeper=lambda s: None)
    assert (
        stats.feeds == 2 and stats.feeds_ok == 1 and stats.new == 3 and stats.matched == 2
    )  # d4 too old
    rows = {r["guid"]: r for r in conn.execute("SELECT * FROM deal_posts")}
    assert rows["d1"]["watch"] == "Europe" and rows["d1"]["price_cents"] == 41200
    assert rows["d3"]["watch"] is None
    log = conn.execute("SELECT feed, ok FROM feed_log ORDER BY id").fetchall()
    assert [(r["feed"], r["ok"]) for r in log] == [
        ("https://rss/feed", 1),
        ("https://atom/feed", 0),
    ]

    # Second poll: nothing new, no duplicate rows.
    stats = deals.poll(
        conn,
        cfg,
        transport=FakeTransport([(200, None, RSS), (200, None, ATOM)]),
        now=NOW,
        verbose=False,
        sleeper=lambda s: None,
    )
    assert stats.new == 1 and conn.execute("SELECT COUNT(*) FROM deal_posts").fetchone()[0] == 4


def test_alert_sends_once_and_digests_over_cap(tmp_path):
    cfg = _cfg(tmp_path, max_alerts_per_run=1)
    conn, cfg = make_db(tmp_path, cfg)
    deals.poll(
        conn,
        cfg,
        transport=FakeTransport([(200, None, RSS), (200, None, ATOM)]),
        now=NOW,
        verbose=False,
        sleeper=lambda s: None,
    )
    stats = deals.alert(conn, cfg, verbose=False, now=NOW)
    assert stats.alerted == 3
    title, body, url = conn.execute(
        "SELECT title, body, url FROM notifications WHERE kind = 'deal'"
    ).fetchone()
    assert "3 announced deals" in title and "Paris" in body and "Rome" in body
    assert url == "/deals"  # a tap on the push opens Deals
    assert deals.alert(conn, cfg, verbose=False, now=NOW).alerted == 0  # all marked alerted_at
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM deal_posts WHERE alerted_at IS NULL AND watch IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_disabled_deals_is_a_noop(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    assert deals.run(conn, cfg, verbose=False).feeds == 0


# ---------------------------------------------------------------------------
# M1: dead feeds, The Flight Deal's headline shape, the empty-feed warning
# ---------------------------------------------------------------------------

TFD_TITLE = (
    "Alaska Air: Seattle – San Diego (and vice versa). $127 (Basic Economy) / $237 (Regular "
    "Economy). "
    "Roundtrip, including all Taxes"
)
TFD_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>The Flight Deal</title>
<item><title>{TFD_TITLE}</title><link>https://www.theflightdeal.com/2026/11/14/sea-san/</link>
<guid isPermaLink="false">https://www.theflightdeal.com/?p=1</guid>
<pubDate>Sat, 14 Nov 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""


def test_flight_deal_headline_takes_the_first_price():
    assert deals.price_from_text(TFD_TITLE) == 12700
    item = deals.parse_feed(TFD_RSS, "https://www.theflightdeal.com/feed/")[0]
    assert item.title == TFD_TITLE
    w = DealWatch(
        "Cheap from Seattle", origins=("Seattle",), destinations=("San Diego",), max_price=15000
    )
    assert deals.matches(item, w)
    assert not deals.matches(item, replace(w, max_price=12000))


def test_html_body_is_a_failure_not_an_empty_feed(tmp_path):
    cfg = _cfg(tmp_path)
    conn, cfg = make_db(tmp_path, cfg)
    home_page = (
        "\n  <!DOCTYPE html>\n<html lang='en'><head><title>Secret "
        "Flying</title></head><body></body></html>"
    )
    t = FakeTransport(
        [
            (200, "https://www.secretflying.com/", home_page),
            (200, None, "<HTML><body>moved</body></HTML>"),
        ]
    )
    stats = deals.poll(conn, cfg, transport=t, now=NOW, verbose=False, sleeper=lambda s: None)
    assert stats.feeds == 2 and stats.feeds_ok == 0
    errors = [r["error"] for r in conn.execute("SELECT error FROM feed_log WHERE ok = 0")]
    assert errors == ["feed returned a web page, not a feed"] * 2
    assert deals.looks_like_html("<?xml version='1.0'?><rss/>") is False


def test_run_uses_the_given_clock(tmp_path):
    cfg = _cfg(tmp_path)
    conn, cfg = make_db(tmp_path, cfg)
    t = FakeTransport([(200, None, RSS), (200, None, ATOM)])
    stats = deals.run(conn, cfg, transport=t, now=NOW, verbose=False, sleeper=lambda s: None)
    assert stats.new == 4 and stats.alerted == 3
    stamps = {
        r[0]
        for r in conn.execute(
            "SELECT seen_at FROM deal_posts UNION SELECT alerted_at FROM deal_posts "
            "WHERE alerted_at IS NOT NULL UNION SELECT at FROM feed_log"
        )
    }
    assert stamps == {"2026-09-17T00:00:00Z"}


def test_health_warns_when_a_feed_is_empty_for_a_week(tmp_path):
    from datetime import timedelta

    from flighttrack import health

    cfg = _cfg(tmp_path)
    conn, cfg = make_db(tmp_path, cfg)

    def log(feed, days_ago, items):
        at = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO feed_log (feed, at, ok, items) VALUES (?, ?, 1, ?)", (feed, at, items)
        )

    for d in (10, 6, 3, 0):
        log("https://rss/feed", d, 16 if d == 10 else 0)  # had items, then nothing for a week
        log("https://atom/feed", d, 0 if d else 2)  # empty for days, then items again today
    conn.commit()
    codes = {p.code for p in health.check(conn, cfg, now=NOW).problems}
    assert "feed_empty_rss" in codes and "feed_empty_atom" not in codes

    # A feed added three days ago is not judged yet.
    conn2, cfg2 = make_db(tmp_path / "b", _cfg(tmp_path))
    conn2.execute(
        "INSERT INTO feed_log (feed, at, ok, items) VALUES ('https://rss/feed', ?, 1, 0)",
        ((NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    conn2.commit()
    assert not any(
        p.code.startswith("feed_empty") for p in health.check(conn2, cfg2, now=NOW).problems
    )


ATOM_SMALL = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>r/flightdeals</title>
<entry><id>t3_1</id><title>SEA to HNL $199</title></entry>
<entry><id>t3_2</id><title>SEA to LIH $219</title></entry>
</feed>"""


PAGE_URL = "https://escape.flights/category/flights-from-seattle/"
PAGE = (Path(__file__).parent / "fixtures" / "pages" / "escape-flights-seattle.html").read_text()


def test_inspect_source_reads_feeds_by_their_items():
    found = deals.inspect_source("https://a/feed", FakeTransport([(200, None, RSS)]))
    assert (found.url, found.kind, found.items) == ("https://a/feed", "feed", 4)
    assert (
        deals.inspect_source("https://a/atom", FakeTransport([(200, None, ATOM_SMALL)])).items == 2
    )


def test_inspect_source_reads_a_page_with_no_feed_as_a_list_of_posts():
    found = deals.inspect_source(PAGE_URL, FakeTransport([(200, None, PAGE)]))
    assert (found.url, found.kind, found.items) == (PAGE_URL, "page", 3)


def test_inspect_source_prefers_the_feed_a_page_advertises():
    page = (
        "<!doctype html><html><head>"
        '<link rel="alternate" type="application/rss+xml" title="Site &raquo; Comments Feed" '
        'href="/comments/feed/">'
        '<link rel="alternate" type="application/rss+xml" title="Seattle deals" href="feed/">'
        "</head><body><h2>Nothing to read here</h2></body></html>"
    )
    t = FakeTransport([(200, None, page), (200, None, RSS)])
    found = deals.inspect_source("https://deals.example/seattle/", t)
    assert (found.url, found.kind) == ("https://deals.example/seattle/feed/", "feed")
    assert [c["url"] for c in t.calls] == [  # the comments feed is skipped
        "https://deals.example/seattle/",
        "https://deals.example/seattle/feed/",
    ]


def test_inspect_source_refuses_what_it_cannot_read():
    import pytest

    from flighttrack.gflights.transport import TransportError

    empty_page = "<!doctype html><html><body><h1>Welcome</h1><p>No posts.</p></body></html>"
    for reply, says in [
        ((200, None, empty_page), "no list of posts"),
        ((503, None, ""), "HTTP 503"),
        ((200, None, '{"items": []}'), "neither a feed nor a web page"),
        (TransportError("timed out"), r"couldn't fetch it \(timed out\)"),
    ]:
        with pytest.raises(deals.FeedError, match=says):
            deals.inspect_source("https://a/x", FakeTransport([reply]))


def test_parse_page_reads_each_post_heading_and_skips_the_rest():
    items = deals.parse_page(PAGE, PAGE_URL)
    assert [i.title.split(" $")[0] for i in items] == [
        "Cheap Flights: Seattle to London",
        "Nonstop Flights: Seattle to/from San Francisco, Los Angeles, Phoenix, or San Diego",
        "Nonstop Flights: Seattle to Salt Lake City",
    ]
    assert [deals.price_from_text(i.title) for i in items] == [58300, 9700, 13400]
    first = items[0]
    assert first.link == first.guid
    assert first.link.startswith("https://escape.flights/cheap-flights-seattle-to-london-")
    assert first.summary.startswith("Icelandair has round-trip flights from Seattle (SEA)")
    assert first.published is None and first.feed == PAGE_URL
    # Not posts: the site title, category, pagination and sidebar links, another site,
    # and markup inside a script.
    assert not any("category" in i.link or "twitter" in i.link for i in items)
    assert not any("not-a-post" in i.link for i in items)


def test_months_named_in_a_post():
    assert deals.months_in("Seattle to London $583 [Nov-May, Aug]") == {11, 12, 1, 2, 3, 4, 5, 8}
    assert deals.months_in("Flights November to May and also August") == {
        11, 12, 1, 2, 3, 4, 5, 8
    }  # fmt: skip
    assert deals.months_in("travel Jan – Mar 2027") == {1, 2, 3}
    assert deals.months_in("Hawaii this summer from $298") == {6, 7, 8}
    assert deals.months_in("Sept and Oct dates") == {9, 10}
    # Ordinary words are not months; no month named is None, not "every month".
    assert deals.months_in("prices may fall; march to the gate") is None
    assert deals.months_in("Seattle to Rome from $489") is None


def test_a_watch_with_months_skips_posts_for_other_months():
    summer_europe = DealWatch(
        "Europe in summer", origins=("Seattle",), destinations=("europe",), months=(6, 7, 8)
    )

    def post(title):
        return deals.FeedItem("g", title, None, None, "", "f")

    assert deals.matches(post("Seattle to Paris $412 [Jun-Aug]"), summer_europe)
    assert deals.matches(post("Seattle to London $583 [Nov-May, Aug]"), summer_europe)
    assert not deals.matches(post("Seattle to Rome $489 [Jan-Mar]"), summer_europe)
    assert deals.matches(post("Seattle to Rome $489"), summer_europe)  # no months: can't tell


def test_a_page_source_records_its_posts_first_then_alerts_on_new_ones(tmp_path):
    """The first read of a page only records what is there (its posts have no dates, so
    old ones must not alert); a post that appears later alerts as usual."""
    from flighttrack import watches

    cfg = replace(_cfg(tmp_path), patterns=[])  # no flexible searches to match as well
    conn, cfg = make_db(tmp_path, cfg)
    watches.create_deal_feed(conn, {"url": PAGE_URL, "kind": "page"})
    watches.create_deal_watch(
        conn, {"name": "Europe", "origins": ["Seattle"], "destinations": ["europe"]}
    )
    stats = deals.poll(
        conn, cfg, transport=FakeTransport([(200, None, PAGE)]), now=NOW, verbose=False
    )
    assert (stats.feeds_ok, stats.new, stats.matched) == (1, 3, 1)  # London is in Europe
    rows = conn.execute("SELECT title, watch, alerted_at FROM deal_posts ORDER BY id").fetchall()
    assert rows[0]["watch"] == "Europe" and rows[0]["alerted_at"] is not None
    assert rows[1]["watch"] is None  # every post is kept for the Deals page
    assert deals.pending_alerts(conn, cfg, now=NOW) == []

    newer = PAGE.replace(
        '<div class="entry-meta-top">',
        "<h2 class='excerpt-title'><a href='https://escape.flights/seattle-to-paris-412/'>"
        "Cheap Flights: Seattle to Paris $412 round-trip [Jun-Aug]</a></h2>"
        '<p>Air France from Seattle.</p><div class="entry-meta-top">',
        1,
    )
    stats = deals.poll(
        conn, cfg, transport=FakeTransport([(200, None, newer)]), now=NOW, verbose=False
    )
    assert (stats.new, stats.matched) == (1, 1)
    assert [r["title"] for r in deals.pending_alerts(conn, cfg, now=NOW)] == [
        "Cheap Flights: Seattle to Paris $412 round-trip [Jun-Aug]"
    ]


def test_a_page_that_stops_listing_posts_is_a_failure(tmp_path):
    from flighttrack import watches

    conn, cfg = make_db(tmp_path, _cfg(tmp_path))
    watches.create_deal_feed(conn, {"url": PAGE_URL, "kind": "page"})
    stats = deals.poll(
        conn,
        cfg,
        transport=FakeTransport([(200, None, "<html><body>Redesigned!</body></html>")]),
        now=NOW,
        verbose=False,
    )
    assert stats.feeds_ok == 0 and "no posts could be read" in stats.errors[0]


def test_trips_and_searches_match_deal_posts_to_the_same_place(tmp_path):
    """A post for a trip's or a search's destination alerts for it, under its target and
    in its months, with no deal watch at all."""
    from flighttrack import watches
    from flighttrack.store.db import set_clock

    set_clock(lambda: NOW)
    conn, cfg = make_db(tmp_path, make_config(patterns=[]))
    watches.create_trip(
        conn,
        cfg,
        {
            "name": "London in March",
            "origin": "SEA",
            "destination": "LHR",
            "depart_date": "2027-03-05",
            "return_date": "2027-03-12",
            "target_price": 70000,
        },
    )
    watches.create_search(
        conn,
        cfg,
        {
            "name": "Ski weekends",
            "origin": "SEA",
            "destinations": ["SLC"],
            "depart_dows": ["FRI"],
            "nights": 2,
            "window_start": "2026-12-01",
            "window_end": "2027-02-28",
        },
    )
    matchers = watches.deal_matchers(conn)
    london, sf, salt_lake = deals.parse_page(PAGE, PAGE_URL)
    assert deals.first_match(london, matchers).name == "London in March"  # Mar, $583 ≤ $700
    assert deals.first_match(salt_lake, matchers).name == "Ski weekends"  # Dec, in window
    assert deals.first_match(sf, matchers) is None
    trip = next(m for m in matchers if m.name == "London in March")
    assert trip.origins == ("Seattle", "SEA") and "LHR" in trip.destinations
    assert trip.months == (3,)
    # Over the target: no alert for the trip.
    dear = deals.FeedItem("g", "Seattle to London $900 [Mar]", None, None, "", "f")
    assert deals.first_match(dear, matchers) is None
