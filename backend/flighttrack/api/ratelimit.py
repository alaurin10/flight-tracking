"""Small in-memory rate limits for the public API (one process, no extra dependencies).

`RateLimiter` is a token bucket per client: `rate` requests refill per second up
to `burst`. `LoginThrottle` locks a client out after repeated wrong passwords,
for longer each time, and stops all logins when failures pile up from many
addresses at once.

`client_key` reads the visitor's address from the proxy headers only when
TRUSTED_PROXY is on (the app sits behind Cloudflare or a reverse proxy);
otherwise anyone could pick their own address by sending the header.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request

from flighttrack.settings import Settings

Clock = Callable[[], float]


def now() -> float:
    """The limiter's clock (tests replace it)."""
    return time.monotonic()


MAX_KEYS = 10_000  # forget the oldest clients beyond this, so memory stays bounded


def client_key(request: Request) -> str:
    settings: Settings = request.app.state.settings
    if settings.trusted_proxy:
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, rate: float, burst: float, clock: Clock | None = None) -> None:
        self.rate = rate
        self.burst = burst
        self.clock = clock or (lambda: now())
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = self.clock()
        with self._lock:
            tokens, at = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - at) * self.rate)
            ok = tokens >= cost
            self._buckets[key] = (tokens - cost if ok else tokens, now)
            if len(self._buckets) > MAX_KEYS:
                for old in list(self._buckets)[: MAX_KEYS // 10]:
                    del self._buckets[old]
        return ok

    def check(self, request: Request, cost: float = 1.0) -> None:
        """Raise 429 when this client is over its limit."""
        if not self.allow(client_key(request), cost):
            raise HTTPException(
                429,
                "too many requests; try again in a minute",
                headers={"Retry-After": str(max(1, round(cost / self.rate)))},
            )


class LoginThrottle:
    """Wrong passwords: 5 free tries per client, then a lockout that doubles each time
    (30 s, 1 min, 2 min … up to 1 h). Over `global_max` failures in `global_window_s`
    from everyone together, every login waits until the window clears."""

    FREE_TRIES = 5
    BASE_LOCK_S = 30.0
    MAX_LOCK_S = 3600.0

    def __init__(
        self,
        clock: Clock | None = None,
        global_max: int = 100,
        global_window_s: float = 900.0,
    ) -> None:
        self.clock = clock or (lambda: now())
        self.global_max = global_max
        self.global_window_s = global_window_s
        self._fails: dict[str, tuple[int, float]] = {}  # key -> (failures, locked until)
        self._recent: deque[float] = deque()
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> float:
        """Seconds this client must wait before trying again (0 = go ahead)."""
        now = self.clock()
        with self._lock:
            while self._recent and self._recent[0] < now - self.global_window_s:
                self._recent.popleft()
            if len(self._recent) >= self.global_max:
                return self._recent[0] + self.global_window_s - now
            _, until = self._fails.get(key, (0, 0.0))
            return max(0.0, until - now)

    def failed(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            self._recent.append(now)
            count, _ = self._fails.get(key, (0, 0.0))
            count += 1
            until = 0.0
            if count >= self.FREE_TRIES:
                wait = self.BASE_LOCK_S * 2 ** (count - self.FREE_TRIES)
                until = now + min(self.MAX_LOCK_S, wait)
            self._fails[key] = (count, until)
            if len(self._fails) > MAX_KEYS:
                for old in list(self._fails)[: MAX_KEYS // 10]:
                    del self._fails[old]

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
