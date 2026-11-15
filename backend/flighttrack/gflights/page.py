"""Turn an HTTP response into one of a handful of named outcomes.

The whole point: the fetcher must react differently to "Google says no
inventory" (fine, move on), "Google is rate-limiting us" (stop the run, cool
off), "the page layout changed" (stop wasting requests, tell a human) and "we
never reached Google" (retry once, then stop). A scraper that folds all four
into one exception cannot be operated safely.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .transport import Response


class PageKind(Enum):
    RESULTS = "results"            # ds:1 payload present and parsed
    NO_RESULTS = "no_results"      # Google answered: no itineraries for this search
    CONSENT = "consent"            # bounced to the EU consent interstitial
    BLOCKED = "blocked"            # 429 / sorry page / unusual-traffic captcha
    ERROR_STATUS = "error_status"  # some other non-2xx
    LAYOUT_UNKNOWN = "layout"      # 200 OK, but no data blob where it should be


@dataclass
class Page:
    kind: PageKind
    detail: str = ""
    payload: Any = None            # the decoded ds:1 JSON, for RESULTS only
    status: int = 0
    url: str = ""
    size: int = 0
    signals: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind is PageKind.RESULTS


# The data blob lives in a <script class="ds:1"> element. Match either the
# element or the bare callback, since only the second survives a minified
# rewrite of the surrounding markup.
_SCRIPT_RE = re.compile(r"<script[^>]*class=\"ds:1\"[^>]*>(.*?)</script>", re.S)
_CALLBACK_RE = re.compile(r"AF_initDataCallback\(\{key:\s*'ds:1'.*?\}\);", re.S)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

BLOCK_SIGNALS = (
    "unusual traffic from your computer network",
    "our systems have detected unusual traffic",
    "/sorry/index",
    "recaptcha",
    "to continue, please type the characters",
)
CONSENT_SIGNALS = ("consent.google.com", "before you continue to google")


def _title(html: str) -> str:
    m = _TITLE_RE.search(html)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else ""


def extract_ds1(html: str) -> tuple[str | None, list[str]]:
    """Return the raw text of the ds:1 callback and the strategies that hit."""
    hits: list[str] = []
    m = _SCRIPT_RE.search(html)
    if m:
        hits.append("script.ds:1")
        return m.group(1), hits
    m = _CALLBACK_RE.search(html)
    if m:
        hits.append("AF_initDataCallback")
        return m.group(0), hits
    return None, hits


def parse_callback(js: str) -> Any:
    """`AF_initDataCallback({key:'ds:1', hash:'…', data:[…], sideChannel:{}});` → the data.

    Raises ValueError for the no-results marker and json errors for anything
    that is not the expected shape.
    """
    if "data:" not in js:
        raise ValueError("callback has no data: member")
    body = js.split("data:", 1)[1].strip()
    if "errorHasStatus: true" in body[:200_000] and not body.startswith("[["):
        raise NoResultsMarker()
    # Everything after the data array is `, sideChannel: {}});`. Rather than
    # guess where it starts, decode exactly one JSON value from the front.
    try:
        value, _end = json.JSONDecoder().raw_decode(body)
    except json.JSONDecodeError:
        # Reference-implementation fallback: cut at the last top-level comma.
        value = json.loads(body.rsplit(",", 1)[0].strip())
    return value


class NoResultsMarker(ValueError):
    pass


def classify(resp: Response) -> Page:
    """Decide what the response is. Never raises.

    Order matters: a page that carries the data blob is RESULTS whatever else
    it contains, so a stray word like "recaptcha" in a script can never be
    mistaken for a block and trigger a cooldown.
    """
    html = resp.text or ""
    low = html[:200_000].lower()
    url_low = (resp.url or "").lower()
    base = dict(status=resp.status, url=resp.url, size=len(html))

    js, hits = extract_ds1(html)
    if js is not None and resp.status < 400:
        try:
            payload = parse_callback(js)
        except NoResultsMarker:
            return Page(PageKind.NO_RESULTS, "Google reported no itineraries (errorHasStatus)", **base)
        except (ValueError, json.JSONDecodeError) as exc:
            return Page(PageKind.LAYOUT_UNKNOWN, f"ds:1 blob found but not decodable: {exc}", signals=hits, **base)
        return Page(PageKind.RESULTS, f"ds:1 via {hits[0]}", payload=payload, signals=hits, **base)

    if "consent.google.com" in url_low or any(s in low for s in CONSENT_SIGNALS):
        return Page(PageKind.CONSENT, "redirected to the consent interstitial", **base)

    which = [s for s in BLOCK_SIGNALS if s in low]
    if resp.status == 429 or "/sorry/" in url_low or which:
        return Page(PageKind.BLOCKED, f"HTTP {resp.status}; signals={which or ['status']}", signals=which, **base)

    if resp.status >= 400:
        return Page(PageKind.ERROR_STATUS, f"HTTP {resp.status}: {_title(html) or 'no title'}", **base)

    return Page(
        PageKind.LAYOUT_UNKNOWN,
        f"no ds:1 data blob in a {len(html):,}-byte page titled {_title(html)!r}",
        **base,
    )
