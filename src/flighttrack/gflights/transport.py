"""HTTP transport with two interchangeable implementations.

`primp` speaks TLS with a real browser's fingerprint, which is the single most
effective thing against bot heuristics. It is a compiled dependency, so it is
optional: when it is not importable we fall back to stdlib `urllib`, which
has worked against Google Flights for years with an ordinary Chrome user agent
and is guaranteed to be available.

Both honour `HTTPS_PROXY` — urllib natively, primp through the `proxy` argument
which we populate from the environment.
"""

from __future__ import annotations

import gzip
import http.cookiejar
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Protocol

DEFAULT_TIMEOUT = 30.0

# A current desktop Chrome on macOS. Google serves the no-JS results page to
# it; exotic or very old UAs get a degraded page without the data blob.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Pre-accepted consent cookie. Irrelevant from US IPs; from EU IPs it stops
# the redirect to consent.google.com that would otherwise hide the results.
CONSENT_COOKIES = "SOCS=CAISHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiA_LyaBg; CONSENT=PENDING+987"

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cookie": CONSENT_COOKIES,
}


class TransportError(Exception):
    """Could not complete the HTTP exchange at all (DNS, TCP, TLS, timeout, proxy)."""


@dataclass
class Response:
    status: int
    url: str            # final URL after redirects — a consent bounce shows here
    text: str
    elapsed: float
    transport: str
    headers: dict[str, str]

    @property
    def size(self) -> int:
        return len(self.text)


class Transport(Protocol):
    name: str

    def get(self, url: str, params: dict[str, str] | None = None,
            headers: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT) -> Response: ...

    def post(self, url: str, data: str, params: dict[str, str] | None = None,
             headers: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT) -> Response: ...


def _with_params(url: str, params: dict[str, str] | None) -> str:
    if not params:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + urllib.parse.urlencode(params)


def _decode_body(raw: bytes, encoding_header: str | None) -> str:
    enc = (encoding_header or "").lower()
    try:
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        pass  # Not actually compressed; fall through and decode as-is.
    return raw.decode("utf-8", errors="replace")


class UrllibTransport:
    """Stdlib only. Always available."""

    name = "urllib"

    def __init__(self) -> None:
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))

    def _do(self, req: urllib.request.Request, timeout: float) -> Response:
        t0 = time.monotonic()
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                return Response(
                    status=resp.status,
                    url=resp.geturl(),
                    text=_decode_body(raw, resp.headers.get("Content-Encoding")),
                    elapsed=time.monotonic() - t0,
                    transport=self.name,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            # 4xx/5xx still carry a body worth classifying (a 429 or a sorry page).
            raw = exc.read() if hasattr(exc, "read") else b""
            return Response(
                status=exc.code,
                url=exc.geturl() or req.full_url,
                text=_decode_body(raw, exc.headers.get("Content-Encoding") if exc.headers else None),
                elapsed=time.monotonic() - t0,
                transport=self.name,
                headers={k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])},
            )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc

    def get(self, url, params=None, headers=None, timeout=DEFAULT_TIMEOUT) -> Response:
        req = urllib.request.Request(_with_params(url, params), headers={**BASE_HEADERS, **(headers or {})})
        return self._do(req, timeout)

    def post(self, url, data, params=None, headers=None, timeout=DEFAULT_TIMEOUT) -> Response:
        req = urllib.request.Request(
            _with_params(url, params),
            data=data.encode("utf-8"),
            method="POST",
            headers={**BASE_HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                     **(headers or {})},
        )
        return self._do(req, timeout)


class PrimpTransport:
    """Browser TLS fingerprint via `primp` (optional dependency)."""

    name = "primp"

    def __init__(self, impersonate: str = "chrome_145", impersonate_os: str = "macos") -> None:
        import primp  # noqa: F401  (ImportError propagates to the chooser)

        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        self._client = primp.Client(
            impersonate=impersonate,
            impersonate_os=impersonate_os,
            referer=True,
            proxy=proxy,
            cookie_store=True,
            timeout=DEFAULT_TIMEOUT,
        )

    def _wrap(self, fn, url, timeout, **kw) -> Response:
        t0 = time.monotonic()
        try:
            res = fn(url, timeout=timeout, **kw)
        except Exception as exc:  # primp raises its own hierarchy
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        return Response(
            status=int(getattr(res, "status_code", 0) or 0),
            url=str(getattr(res, "url", url)),
            text=getattr(res, "text", "") or "",
            elapsed=time.monotonic() - t0,
            transport=self.name,
            headers={str(k).lower(): str(v) for k, v in dict(getattr(res, "headers", {}) or {}).items()},
        )

    def get(self, url, params=None, headers=None, timeout=DEFAULT_TIMEOUT) -> Response:
        return self._wrap(self._client.get, url, timeout, params=params or {},
                          headers={"Cookie": CONSENT_COOKIES, **(headers or {})})

    def post(self, url, data, params=None, headers=None, timeout=DEFAULT_TIMEOUT) -> Response:
        return self._wrap(
            self._client.post, _with_params(url, params), timeout, content=data.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                     "Cookie": CONSENT_COOKIES, **(headers or {})},
        )


def default_transport(prefer: str = "auto") -> Transport:
    """`auto` = primp when importable, else urllib. `urllib`/`primp` force one."""
    if prefer == "urllib":
        return UrllibTransport()
    if prefer in ("auto", "primp"):
        try:
            return PrimpTransport()
        except ImportError:
            if prefer == "primp":
                raise
            return UrllibTransport()
    raise ValueError(f"unknown transport {prefer!r}")
