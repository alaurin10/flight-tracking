"""Deal feeds: parsing, matching, polling, alerting."""

from dataclasses import replace
from datetime import UTC, datetime

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
    cfg = make_config(notify={"channel": "file", "file": {"path": str(tmp_path / "digest.md")}})
    return replace(
        cfg,
        deals=Deals(
            enabled=True, feeds=("https://rss/feed", "https://atom/feed"), watches=watches, **over
        ),
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
    assert deals.first_match(items["d1"], cfg.deals.watches).name == "Europe"
    assert deals.first_match(items["d2"], cfg.deals.watches).name == "Japan any price"
    assert deals.first_match(items["d3"], cfg.deals.watches) is None  # Chicago
    assert deals.first_match(items["d4"], cfg.deals.watches) is None  # over max price
    rome = deals.parse_feed(ATOM, "f")[0]
    assert deals.first_match(rome, cfg.deals.watches).name == "Europe"  # region word + West Coast


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
    text = (tmp_path / "digest.md").read_text()
    assert "3 announced deals" in text and "Paris" in text and "Rome" in text
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
