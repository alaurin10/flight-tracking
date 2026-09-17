"""Announced-deal watcher: RSS/Atom feeds from deal sites, matched to what you care about.

This is the third kind of question — "tell me when someone announces a deal
to Europe from my city" — and it needs a different source than the price
grid. Deal sites publish RSS feeds *on purpose*, so this is the one
completely sanctioned, completely stable input the system has: a feed URL,
stdlib XML parsing, and word matching on titles.

Matching is deliberately simple and transparent: a post matches a watch if
any of the watch's origin words appears, any of its destination words (or
the words of a named region) appears, and the price parsed from the title is
at or under `max_price` when one is set. There is no NLP to be wrong.
"""

from __future__ import annotations

import html
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .config import Config, DealWatch
from .db import utcnow
from .gflights.transport import Transport, TransportError, default_transport
from .notify import NotifyError, notify
from .report import fmt_money

# A region name in `destinations` expands to these words (matched as whole
# words, case-insensitively). Extend freely — it is only a list.
REGIONS: dict[str, tuple[str, ...]] = {
    "europe": (
        "europe", "european", "uk", "england", "london", "manchester", "edinburgh", "scotland", "ireland", "dublin",
        "france", "paris", "nice", "lyon", "spain", "madrid", "barcelona", "malaga", "seville", "portugal", "lisbon",
        "porto", "italy", "rome", "milan", "venice", "florence", "naples", "germany", "berlin", "munich", "frankfurt",
        "netherlands", "amsterdam", "belgium", "brussels", "switzerland", "zurich", "geneva", "austria", "vienna",
        "czech", "prague", "poland", "warsaw", "krakow", "hungary", "budapest", "greece", "athens", "croatia",
        "zagreb", "split", "dubrovnik", "denmark", "copenhagen", "sweden", "stockholm", "norway", "oslo", "finland",
        "helsinki", "iceland", "reykjavik", "turkey", "istanbul", "romania", "bucharest", "bulgaria", "sofia",
        "malta", "cyprus", "estonia", "tallinn", "latvia", "riga", "lithuania", "vilnius", "slovenia", "ljubljana",
        "serbia", "belgrade", "scandinavia",
    ),
    "japan": ("japan", "tokyo", "osaka", "kyoto", "nagoya", "fukuoka", "sapporo", "okinawa", "haneda", "narita"),
    "asia": (
        "asia", "japan", "tokyo", "osaka", "korea", "seoul", "taiwan", "taipei", "china", "beijing", "shanghai",
        "hong kong", "singapore", "thailand", "bangkok", "phuket", "vietnam", "hanoi", "saigon", "ho chi minh",
        "malaysia", "kuala lumpur", "indonesia", "bali", "jakarta", "philippines", "manila", "cebu", "india",
        "delhi", "mumbai", "sri lanka", "cambodia", "laos", "nepal", "kathmandu",
    ),
    "hawaii": ("hawaii", "honolulu", "maui", "kauai", "kona", "hilo", "oahu"),
    "mexico": ("mexico", "cancun", "cabo", "puerto vallarta", "mexico city", "guadalajara", "oaxaca", "tulum", "cozumel"),
    "caribbean": ("caribbean", "jamaica", "bahamas", "aruba", "puerto rico", "san juan", "dominican", "punta cana",
                  "barbados", "st. lucia", "saint lucia", "turks", "cayman", "curacao", "bermuda", "antigua"),
    "south america": ("south america", "brazil", "rio", "sao paulo", "argentina", "buenos aires", "chile", "santiago",
                      "peru", "lima", "cusco", "colombia", "bogota", "medellin", "cartagena", "ecuador", "quito",
                      "galapagos", "uruguay", "montevideo", "bolivia"),
    "oceania": ("australia", "sydney", "melbourne", "brisbane", "perth", "new zealand", "auckland", "queenstown", "fiji", "tahiti"),
    "africa": ("africa", "morocco", "marrakech", "casablanca", "egypt", "cairo", "south africa", "cape town",
               "johannesburg", "kenya", "nairobi", "tanzania", "zanzibar", "ghana", "accra", "senegal"),
}

# Words that mean "from where you are" in US deal-site headlines. A watch
# usually lists its own city plus a few of these.
US_WIDE = ("us cities", "u.s. cities", "many us cities", "nationwide", "west coast", "usa", "united states")

_PRICE = re.compile(r"(?:US\s?)?\$\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d\d)?")


@dataclass
class FeedItem:
    guid: str
    title: str
    link: str | None
    published: str | None      # ISO UTC
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
        s = f"feeds={self.feeds_ok}/{self.feeds} items={self.items} new={self.new} matched={self.matched} alerted={self.alerted}"
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
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        items.append(FeedItem(guid=guid, title=title, link=link, published=published, summary=summary, feed=feed_url))
    return items


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

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
    return True


def first_match(item: FeedItem, watches) -> DealWatch | None:
    for w in watches:
        if matches(item, w):
            return w
    return None


# ---------------------------------------------------------------------------
# Poll + alert
# ---------------------------------------------------------------------------

def poll(conn: sqlite3.Connection, cfg: Config, transport: Transport | None = None,
         now: datetime | None = None, verbose: bool = True, sleeper=time.sleep) -> DealStats:
    stats = DealStats()
    now = now or datetime.now(timezone.utc)
    transport = transport or default_transport("urllib")
    cutoff = (now - timedelta(days=cfg.deals.lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for i, feed in enumerate(cfg.deals.feeds):
        if i:
            sleeper(1.0)
        stats.feeds += 1
        try:
            resp = transport.get(feed, timeout=cfg.fetch.request_timeout_seconds)
            if resp.status >= 400:
                raise TransportError(f"HTTP {resp.status}")
            items = parse_feed(resp.text, feed)
        except (TransportError, ET.ParseError) as exc:
            msg = f"{type(exc).__name__}: {exc}"[:300]
            stats.errors.append(f"{feed}: {msg}")
            conn.execute("INSERT INTO feed_log (feed, at, ok, items, error) VALUES (?, ?, 0, 0, ?)", (feed, utcnow(), msg))
            conn.commit()
            if verbose:
                print(f"  [FAIL] {feed}: {msg}")
            continue

        stats.feeds_ok += 1
        stats.items += len(items)
        conn.execute("INSERT INTO feed_log (feed, at, ok, items) VALUES (?, ?, 1, ?)", (feed, utcnow(), len(items)))
        new_here = 0
        for it in items:
            if it.published and it.published < cutoff:
                continue
            watch = first_match(it, cfg.deals.watches)
            cur = conn.execute(
                """
                INSERT INTO deal_posts (guid, feed, title, link, published_at, seen_at, price_cents, watch)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(guid) DO NOTHING
                """,
                (it.guid, feed, it.title, it.link, it.published, utcnow(),
                 price_from_text(it.title) or price_from_text(it.summary), watch.name if watch else None),
            )
            if cur.rowcount:
                new_here += 1
                if watch:
                    stats.matched += 1
        stats.new += new_here
        conn.commit()
        if verbose:
            print(f"  [ok] {feed}: {len(items)} items, {new_here} new")
    return stats


def pending_alerts(conn: sqlite3.Connection, cfg: Config) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cfg.deals.lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def alert(conn: sqlite3.Connection, cfg: Config, dry_run: bool = False, verbose: bool = True) -> DealStats:
    stats = DealStats()
    rows = pending_alerts(conn, cfg)
    if not rows:
        return stats
    try:
        if len(rows) <= cfg.deals.max_alerts_per_run:
            for r in rows:
                subject = f"✈ deal: {r['title'][:90]}"
                if dry_run:
                    print(f"  [dry-run] {_line(r)}")
                else:
                    notify(cfg.notify, subject, _line(r) + (f"\n{r['link']}" if r["link"] else ""), r["link"])
                stats.alerted += 1
        else:
            body = "\n\n".join(_line(r) + (f"\n{r['link']}" if r["link"] else "") for r in rows)
            subject = f"✈ {len(rows)} announced deals match your watches"
            if dry_run:
                print(f"  [dry-run digest] {subject}\n{body}")
            else:
                notify(cfg.notify, subject, body, rows[0]["link"])
            stats.alerted = len(rows)
    except NotifyError as exc:
        stats.delivery_error = str(exc)
        if verbose:
            print(f"[deals] DELIVERY FAILED: {exc}")
        return stats
    if not dry_run:
        now = utcnow()
        conn.executemany("UPDATE deal_posts SET alerted_at = ? WHERE id = ?", [(now, r["id"]) for r in rows])
        conn.commit()
    return stats


def run(conn: sqlite3.Connection, cfg: Config, transport: Transport | None = None,
        dry_run: bool = False, verbose: bool = True) -> DealStats:
    if not cfg.deals.enabled:
        if verbose:
            print("[deals] disabled in config")
        return DealStats()
    stats = poll(conn, cfg, transport, verbose=verbose)
    a = alert(conn, cfg, dry_run=dry_run, verbose=verbose)
    stats.alerted, stats.delivery_error = a.alerted, a.delivery_error
    if verbose:
        print(f"[deals] {stats.summary()}")
    return stats
