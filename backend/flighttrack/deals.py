"""Announced-deal watcher: deal sites' feeds and pages, matched to what you care about.

This is the third kind of question — "tell me when someone announces a deal
to Europe from my city" — and it needs a different source than the price
grid. Deal sites publish RSS feeds *on purpose*, so this is the one
completely sanctioned, completely stable input the system has: a feed URL,
stdlib XML parsing, and word matching on titles.

Matching is deliberately simple and transparent: a post matches a watch if
any of the watch's origin words appears, any of its destination words (or
the words of a named region) appears, and the price parsed from the title is
at or under `max_price` when one is set, and, when the watch names travel
months, the post names one of them or none at all. There is no NLP to be wrong.

A source is an RSS/Atom feed, or a web page with no feed (escape.flights'
category pages): each heading that links to a post on the same site is one
post, and the text after it, up to the next, is its summary. Trips and
flexible searches are matched too (`watches.deal_matchers`).
"""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import partial
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .config import Config, DealWatch
from .gflights.transport import Transport, TransportError, default_transport
from .notifier import Notifier
from .report import fmt_money
from .store.db import now as clock_now
from .watches import active_sources, deal_matchers

log = logging.getLogger(__name__)

# A region name in `destinations` expands to these words (matched as whole
# words, case-insensitively). Extend freely — it is only a list.
REGIONS: dict[str, tuple[str, ...]] = {
    "europe": (
        "europe",
        "european",
        "uk",
        "england",
        "london",
        "manchester",
        "edinburgh",
        "scotland",
        "ireland",
        "dublin",
        "france",
        "paris",
        "nice",
        "lyon",
        "spain",
        "madrid",
        "barcelona",
        "malaga",
        "seville",
        "portugal",
        "lisbon",
        "porto",
        "italy",
        "rome",
        "milan",
        "venice",
        "florence",
        "naples",
        "germany",
        "berlin",
        "munich",
        "frankfurt",
        "netherlands",
        "amsterdam",
        "belgium",
        "brussels",
        "switzerland",
        "zurich",
        "geneva",
        "austria",
        "vienna",
        "czech",
        "prague",
        "poland",
        "warsaw",
        "krakow",
        "hungary",
        "budapest",
        "greece",
        "athens",
        "croatia",
        "zagreb",
        "split",
        "dubrovnik",
        "denmark",
        "copenhagen",
        "sweden",
        "stockholm",
        "norway",
        "oslo",
        "finland",
        "helsinki",
        "iceland",
        "reykjavik",
        "turkey",
        "istanbul",
        "romania",
        "bucharest",
        "bulgaria",
        "sofia",
        "malta",
        "cyprus",
        "estonia",
        "tallinn",
        "latvia",
        "riga",
        "lithuania",
        "vilnius",
        "slovenia",
        "ljubljana",
        "serbia",
        "belgrade",
        "scandinavia",
    ),
    "japan": (
        "japan",
        "tokyo",
        "osaka",
        "kyoto",
        "nagoya",
        "fukuoka",
        "sapporo",
        "okinawa",
        "haneda",
        "narita",
    ),
    "asia": (
        "asia",
        "japan",
        "tokyo",
        "osaka",
        "korea",
        "seoul",
        "taiwan",
        "taipei",
        "china",
        "beijing",
        "shanghai",
        "hong kong",
        "singapore",
        "thailand",
        "bangkok",
        "phuket",
        "vietnam",
        "hanoi",
        "saigon",
        "ho chi minh",
        "malaysia",
        "kuala lumpur",
        "indonesia",
        "bali",
        "jakarta",
        "philippines",
        "manila",
        "cebu",
        "india",
        "delhi",
        "mumbai",
        "sri lanka",
        "cambodia",
        "laos",
        "nepal",
        "kathmandu",
    ),
    "hawaii": ("hawaii", "honolulu", "maui", "kauai", "kona", "hilo", "oahu"),
    "mexico": (
        "mexico",
        "cancun",
        "cabo",
        "puerto vallarta",
        "mexico city",
        "guadalajara",
        "oaxaca",
        "tulum",
        "cozumel",
    ),
    "caribbean": (
        "caribbean",
        "jamaica",
        "bahamas",
        "aruba",
        "puerto rico",
        "san juan",
        "dominican",
        "punta cana",
        "barbados",
        "st. lucia",
        "saint lucia",
        "turks",
        "cayman",
        "curacao",
        "bermuda",
        "antigua",
    ),
    "south america": (
        "south america",
        "brazil",
        "rio",
        "sao paulo",
        "argentina",
        "buenos aires",
        "chile",
        "santiago",
        "peru",
        "lima",
        "cusco",
        "colombia",
        "bogota",
        "medellin",
        "cartagena",
        "ecuador",
        "quito",
        "galapagos",
        "uruguay",
        "montevideo",
        "bolivia",
    ),
    "oceania": (
        "australia",
        "sydney",
        "melbourne",
        "brisbane",
        "perth",
        "new zealand",
        "auckland",
        "queenstown",
        "fiji",
        "tahiti",
    ),
    "africa": (
        "africa",
        "morocco",
        "marrakech",
        "casablanca",
        "egypt",
        "cairo",
        "south africa",
        "cape town",
        "johannesburg",
        "kenya",
        "nairobi",
        "tanzania",
        "zanzibar",
        "ghana",
        "accra",
        "senegal",
    ),
}

# Words that mean "from where you are" in US deal-site headlines. A watch
# usually lists its own city plus a few of these.
US_WIDE = (
    "us cities",
    "u.s. cities",
    "many us cities",
    "nationwide",
    "west coast",
    "usa",
    "united states",
)

_PRICE = re.compile(r"(?:US\s?)?\$\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d\d)?")
_HTML_START = re.compile(r"^\s*(?:<!doctype\s+html|<html)", re.IGNORECASE)


class FeedError(Exception):
    """The feed URL answered, but not with a feed."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def looks_like_html(text: str) -> bool:
    """A dead feed often redirects to the site's home page and answers 200."""
    return bool(_HTML_START.match(text or ""))


@dataclass
class FeedItem:
    guid: str
    title: str
    link: str | None
    published: str | None  # ISO UTC
    summary: str
    feed: str


@dataclass
class DealStats:
    feeds: int = 0
    feeds_ok: int = 0
    items: int = 0
    new: int = 0
    matched: int = 0
    alerted: int = 0
    delivery_error: str | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = (
            f"feeds={self.feeds_ok}/{self.feeds} items={self.items} new={self.new} "
            f"matched={self.matched} alerted={self.alerted}"
        )
        if self.delivery_error:
            s += f" DELIVERY_ERROR={self.delivery_error}"
        return s


# ---------------------------------------------------------------------------
# Feed parsing (RSS 2.0 and Atom, namespaces tolerated)
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el: ET.Element, *names: str) -> str | None:
    wanted = {n.lower() for n in names}
    for c in el:
        if _local(c.tag) in wanted:
            txt = (c.text or "").strip()
            if not txt and _local(c.tag) == "link":
                txt = (c.get("href") or "").strip()
            if txt:
                return txt
    return None


def _to_iso(text: str | None) -> str | None:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse_feed(xml_text: str, feed_url: str) -> list[FeedItem]:
    root = ET.fromstring(xml_text)
    items: list[FeedItem] = []
    entries = [e for e in root.iter() if _local(e.tag) in ("item", "entry")]
    for e in entries:
        title = _strip_html(_child_text(e, "title") or "")
        if not title:
            continue
        link = _child_text(e, "link")
        guid = _child_text(e, "guid", "id") or link or f"{feed_url}#{title}"
        published = _to_iso(_child_text(e, "pubDate", "published", "updated", "date"))
        summary = _strip_html(_child_text(e, "description", "summary", "content") or "")[:500]
        items.append(
            FeedItem(
                guid=guid,
                title=title,
                link=link,
                published=published,
                summary=summary,
                feed=feed_url,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Travel months named in a post ("[Nov-May, Aug]", "Flights November to May")
# ---------------------------------------------------------------------------

_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
# Capitalised only, so "may" and "march" as ordinary words don't count.
_M = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_MONTH_RANGE = re.compile(rf"\b({_M})\.?\s*(?:-|–|—|to|through|thru|until)\s*({_M})\b")
_MONTH_ONE = re.compile(rf"\b({_M})\b")
_SEASONS = (
    (re.compile(r"\bsummer\b", re.I), (6, 7, 8)),
    (re.compile(r"\bwinter\b", re.I), (12, 1, 2)),
    (re.compile(r"\b(?:autumn|Fall)\b"), (9, 10, 11)),
    (re.compile(r"\bSpring\b"), (3, 4, 5)),
)


def _month(word: str) -> int:
    return _MONTH_NUM[word[:3].lower()]


def months_in(text: str) -> set[int] | None:
    """The travel months a post names, or None when it names none. A range wraps over
    the new year ("Nov-May" is November to May); seasons count as their months."""
    found: set[int] = set()

    def span(m: re.Match[str]) -> str:
        a, b = _month(m.group(1)), _month(m.group(2))
        n = a
        while True:
            found.add(n)
            if n == b:
                break
            n = n % 12 + 1
        return " "

    rest = _MONTH_RANGE.sub(span, text or "")
    found.update(_month(m.group(1)) for m in _MONTH_ONE.finditer(rest))
    for pattern, months in _SEASONS:
        if pattern.search(rest):
            found.update(months)
    return found or None


# ---------------------------------------------------------------------------
# Deal pages: a web page with no feed, read as a list of posts
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
_HEADINGS = {"h1", "h2", "h3", "h4"}
# Links that lead to more lists, not to a post.
_NOT_A_POST = re.compile(r"/(?:category|tag|author|page|feed|search|wp-login)(?:/|$)|[?#]")


class _PageReader(HTMLParser):
    """Collects (title, link, summary) for each heading that links to a post."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.host = urlsplit(base).netloc.lower().removeprefix("www.")
        self.skip = 0
        self.heading = 0
        self.link: str | None = None
        self.link_text: list[str] = []
        self.anchors = 0  # inside any link: its text is not part of a summary
        self.found: list[tuple[str, str]] = []  # links seen in the current heading
        self.posts: list[dict[str, str]] = []
        self.feeds: list[tuple[str, str]] = []  # <link rel=alternate> (href, title)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag == "link" and "alternate" in a.get("rel", "").lower().split():
            if re.search(r"application/(?:rss|atom)\+xml", a.get("type", ""), re.I):
                self.feeds.append((urljoin(self.base, a.get("href", "")), a.get("title", "")))
        elif tag in _HEADINGS:
            self.heading += 1
            self.found = []
        elif tag == "a":
            self.anchors += 1
            if self.heading:
                self.link = urljoin(self.base, a.get("href", ""))
                self.link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag == "a":
            self.anchors = max(0, self.anchors - 1)
            if self.link is not None:
                self.found.append((self.link, " ".join(self.link_text)))
                self.link = None
        elif tag in _HEADINGS and self.heading:
            self.heading -= 1
            for href, text in self.found:
                title = _strip_html(text)
                if len(title) >= 15 and self._is_post(href):
                    self.posts.append({"title": title, "link": href, "summary": ""})
                    break
            self.found = []

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.link is not None:
            self.link_text.append(data)
        elif self.posts and not self.heading and not self.anchors:
            post = self.posts[-1]
            if len(post["summary"]) < 600:
                post["summary"] += " " + data

    def _is_post(self, href: str) -> bool:
        parts = urlsplit(href)
        host = parts.netloc.lower().removeprefix("www.")
        if parts.scheme not in ("http", "https") or host != self.host:
            return False
        if href.rstrip("/") == self.base.rstrip("/") or parts.path in ("", "/"):
            return False
        return not _NOT_A_POST.search(href[len(parts.scheme) + 3 + len(parts.netloc) :])


def _read_page(text: str, url: str) -> _PageReader:
    reader = _PageReader(url)
    reader.feed(text)
    reader.close()
    return reader


def parse_page(html_text: str, page_url: str) -> list[FeedItem]:
    """The posts listed on a deal site's page: each heading that links to a post on the
    same site, with the text after it (up to the next) as its summary."""
    seen: set[str] = set()
    items: list[FeedItem] = []
    for post in _read_page(html_text, page_url).posts:
        if post["link"] in seen:
            continue
        seen.add(post["link"])
        items.append(
            FeedItem(
                guid=post["link"],
                title=post["title"],
                link=post["link"],
                published=None,
                summary=_strip_html(post["summary"])[:500],
                feed=page_url,
            )
        )
    return items


def _feed_items(text: str, url: str) -> list[FeedItem] | None:
    """The items when `text` is an RSS, Atom or RDF feed, else None."""
    if looks_like_html(text):
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    if _local(root.tag) not in ("rss", "feed", "rdf"):
        return None
    return parse_feed(text, url)


@dataclass(frozen=True)
class SourceFound:
    url: str  # what to poll: the address given, or the feed its page advertises
    kind: str  # "feed" | "page"
    items: int


def inspect_source(url: str, transport: Transport, timeout: float = 15.0) -> SourceFound:
    """Fetch an address once, as when the owner adds a deal source, and work out how to
    read it: a feed; a page that advertises a feed (its feed is used); or a page that
    lists posts. Raises FeedError, in words, when it is none of these."""

    def get(u: str) -> str:
        try:
            resp = transport.get(u, timeout=timeout)
        except TransportError as exc:
            raise FeedError(f"couldn't fetch it ({exc})") from None
        if resp.status >= 400:
            raise FeedError(f"it answered HTTP {resp.status}")
        return resp.text

    text = get(url)
    items = _feed_items(text, url)
    if items is not None:
        return SourceFound(url, "feed", len(items))
    if not looks_like_html(text) and "<" not in text[:200]:
        raise FeedError("it is neither a feed nor a web page")
    reader = _read_page(text, url)
    for href, title in reader.feeds:
        if "comment" in title.lower() or "/comments/" in href:
            continue
        try:
            found = _feed_items(get(href), href)
        except FeedError:
            continue
        if found:
            return SourceFound(href, "feed", len(found))
    posts = parse_page(text, url)
    if posts:
        return SourceFound(url, "page", len(posts))
    raise FeedError("it has no feed, and no list of posts could be read from the page")


def price_from_text(text: str) -> int | None:
    m = _PRICE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", "")) * 100
    except ValueError:
        return None


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?![a-z0-9])", text) is not None


def expand_words(words: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for w in words:
        out.extend(REGIONS.get(w.lower(), (w,)))
    return tuple(dict.fromkeys(out))


def matches(item: FeedItem, watch: DealWatch) -> bool:
    text = f"{item.title} {item.summary}".lower()
    if watch.origins and not any(_has_word(text, w) for w in expand_words(watch.origins)):
        return False
    if watch.destinations and not any(_has_word(text, w) for w in expand_words(watch.destinations)):
        return False
    if watch.keywords and not all(_has_word(text, w) for w in watch.keywords):
        return False
    if watch.max_price is not None:
        price = price_from_text(item.title) or price_from_text(item.summary)
        if price is None or price > watch.max_price:
            return False
    if watch.months:
        named = months_in(f"{item.title} {item.summary}")
        if named is not None and not named & set(watch.months):
            return False
    return True


def first_match(item: FeedItem, watches: Iterable[DealWatch]) -> DealWatch | None:
    for w in watches:
        if matches(item, w):
            return w
    return None


# ---------------------------------------------------------------------------
# Poll + alert
# ---------------------------------------------------------------------------


def poll(
    conn: sqlite3.Connection,
    cfg: Config,
    transport: Transport | None = None,
    now: datetime | None = None,
    verbose: bool = True,
    sleeper: Callable[[float], None] = time.sleep,
) -> DealStats:
    stats = DealStats()
    now = now or clock_now()
    stamp = _iso(now)
    transport = transport or default_transport("urllib")
    cutoff = _iso(now - timedelta(days=cfg.deals.lookback_days))
    watches = deal_matchers(conn)

    for i, (feed, kind) in enumerate(active_sources(conn, cfg)):
        if i:
            sleeper(1.0)
        stats.feeds += 1
        # A page's posts carry no dates: the first good read of a page only records what
        # is already there, so an old post never alerts; alerts start with new posts.
        first_read = (
            kind == "page"
            and conn.execute(
                "SELECT 1 FROM feed_log WHERE feed = ? AND ok = 1 LIMIT 1", (feed,)
            ).fetchone()
            is None
        )
        try:
            resp = transport.get(feed, timeout=cfg.fetch.request_timeout_seconds)
            if resp.status >= 400:
                raise TransportError(f"HTTP {resp.status}")
            if kind == "page":
                items = parse_page(resp.text, feed)
                if not items:
                    raise FeedError("no posts could be read from the page (has it changed?)")
            else:
                if looks_like_html(resp.text):
                    raise FeedError("feed returned a web page, not a feed")
                items = parse_feed(resp.text, feed)
        except (TransportError, FeedError, ET.ParseError) as exc:
            msg = (str(exc) if isinstance(exc, FeedError) else f"{type(exc).__name__}: {exc}")[:300]
            stats.errors.append(f"{feed}: {msg}")
            conn.execute(
                "INSERT INTO feed_log (feed, at, ok, items, error) VALUES (?, ?, 0, 0, ?)",
                (feed, stamp, msg),
            )
            conn.commit()
            if verbose:
                log.warning(f"[FAIL] {feed}: {msg}")
            continue

        stats.feeds_ok += 1
        stats.items += len(items)
        conn.execute(
            "INSERT INTO feed_log (feed, at, ok, items) VALUES (?, ?, 1, ?)",
            (feed, stamp, len(items)),
        )
        new_here = 0
        for it in items:
            if it.published and it.published < cutoff:
                continue
            watch = first_match(it, watches)
            cur = conn.execute(
                """
                INSERT INTO deal_posts
                    (guid, feed, title, link, published_at, seen_at, price_cents, watch,
                     alerted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(guid) DO NOTHING
                """,
                (
                    it.guid,
                    feed,
                    it.title,
                    it.link,
                    it.published,
                    stamp,
                    price_from_text(it.title) or price_from_text(it.summary),
                    watch.name if watch else None,
                    stamp if first_read else None,
                ),
            )
            if cur.rowcount:
                new_here += 1
                if watch:
                    stats.matched += 1
        stats.new += new_here
        conn.commit()
        if verbose:
            log.info(f"[ok] {feed}: {len(items)} items, {new_here} new")
    return stats


def pending_alerts(
    conn: sqlite3.Connection, cfg: Config, now: datetime | None = None
) -> list[sqlite3.Row]:
    now = now or clock_now()
    cutoff = _iso(now - timedelta(days=cfg.deals.lookback_days))
    return conn.execute(
        """
        SELECT * FROM deal_posts
        WHERE watch IS NOT NULL AND alerted_at IS NULL AND seen_at >= ?
        ORDER BY COALESCE(price_cents, 1 << 30) ASC, id ASC
        """,
        (cutoff,),
    ).fetchall()


def _line(row: sqlite3.Row) -> str:
    price = f" · {fmt_money(row['price_cents'])}" if row["price_cents"] else ""
    return f"[{row['watch']}] {row['title']}{price}"


def _mark_alerted(conn: sqlite3.Connection, ids: list[int], stamp: str) -> None:
    conn.executemany("UPDATE deal_posts SET alerted_at = ? WHERE id = ?", [(stamp, i) for i in ids])


def alert(
    conn: sqlite3.Connection,
    cfg: Config,
    dry_run: bool = False,
    verbose: bool = True,
    now: datetime | None = None,
    notifier: Notifier | None = None,
) -> DealStats:
    """Record matched posts as notifications. `deal_posts.alerted_at` is written with
    the notification row; a channel failure is reported in `delivery_error`."""
    stats = DealStats()
    now = now or clock_now()
    rows = pending_alerts(conn, cfg, now)
    if not rows:
        return stats
    if dry_run:
        for r in rows:
            print(f"  [dry-run] {_line(r)}")
        stats.alerted = len(rows)
        return stats

    notifier = notifier or Notifier.from_config(cfg)
    stamp = _iso(now)
    errors: list[str] = []
    if len(rows) <= cfg.deals.max_alerts_per_run:
        for r in rows:
            d = notifier.send(
                conn,
                "deal",
                f"✈ deal: {r['title'][:90]}",
                _line(r) + (f"\n{r['link']}" if r["link"] else ""),
                r["link"],
                record=partial(_mark_alerted, ids=[r["id"]], stamp=stamp),
            )
            errors += d.errors
            stats.alerted += 1
    else:
        d = notifier.send(
            conn,
            "deal",
            f"✈ {len(rows)} announced deals match your watches",
            "\n\n".join(_line(r) + (f"\n{r['link']}" if r["link"] else "") for r in rows),
            rows[0]["link"],
            record=partial(_mark_alerted, ids=[r["id"] for r in rows], stamp=stamp),
        )
        errors += d.errors
        stats.alerted = len(rows)
    if errors:
        stats.delivery_error = "; ".join(dict.fromkeys(errors))
        if verbose:
            log.warning(f"[deals] DELIVERY FAILED (deals are recorded): {stats.delivery_error}")
    return stats


def run(
    conn: sqlite3.Connection,
    cfg: Config,
    transport: Transport | None = None,
    dry_run: bool = False,
    verbose: bool = True,
    now: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    notifier: Notifier | None = None,
) -> DealStats:
    if not cfg.deals.enabled:
        if verbose:
            log.info("[deals] disabled in config")
        return DealStats()
    now = now or clock_now()
    stats = poll(conn, cfg, transport, now=now, verbose=verbose, sleeper=sleeper)
    a = alert(conn, cfg, dry_run=dry_run, verbose=verbose, now=now, notifier=notifier)
    stats.alerted, stats.delivery_error = a.alerted, a.delivery_error
    if verbose:
        log.info(f"[deals] {stats.summary()}")
    return stats
