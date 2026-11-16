"""Alerts on the owner's phone and computer through the W3C Web Push API (M8).

The browser subscribes with our VAPID public key and hands us an endpoint at its push
service (Google, Apple, Mozilla or Microsoft). We encrypt a small JSON payload for that
subscription and POST it there; the service worker shows it. Push is off until
VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY are set (`flighttrack push keys`).

Only the owner subscribes (the site is public and read-only; alerts are the owner's).
Each device chooses which kinds it gets: price alerts, deals (posts and found deals) and
collector health. Copied from Snow Watch's `push.py`, without its digests: Flight Watch
already sends one notification per alert (`notifier.Notifier`).
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from .settings import Settings
from .store.db import utcnow

log = logging.getLogger("flighttrack.push")

KINDS = ("price", "deal", "health")
MAX_SUBSCRIPTIONS = 20
MAX_FAILURES = 5
TTL_SECONDS = 12 * 3600

# The browsers' own push services. The server POSTs to a subscription's endpoint, so
# only these hosts are accepted: anything else would let a caller make the server
# send requests wherever they like.
PUSH_HOSTS = frozenset(
    {
        "fcm.googleapis.com",  # Chrome, Edge, Android
        "android.googleapis.com",
        "updates.push.services.mozilla.com",  # Firefox
        "web.push.apple.com",  # Safari, iOS
    }
)
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")

Kind = Literal["price", "deal", "health"]


def is_push_service(endpoint: str) -> bool:
    try:
        u = urlsplit(endpoint)
        port = u.port
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return (
        u.scheme == "https"
        and port is None
        and not u.username
        and (host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES))
    )


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushSubscription(BaseModel):
    """What `PushManager.subscribe()` returns (its toJSON())."""

    endpoint: str = Field(max_length=1000)
    keys: SubscriptionKeys

    @field_validator("endpoint")
    @classmethod
    def _push_service(cls, v: str) -> str:
        if not is_push_service(v):
            raise ValueError("not a browser push service address")
        return v


class Endpoint(BaseModel):
    """Names a subscription without its keys (unsubscribe, test, prefs)."""

    endpoint: str = Field(max_length=1000)


def _all_kinds() -> list[Kind]:
    return ["price", "deal", "health"]


class AlertPrefs(BaseModel):
    kinds: list[Kind] = Field(default_factory=_all_kinds, max_length=len(KINDS))


class SubscribeIn(AlertPrefs):
    subscription: PushSubscription


class PushOut(BaseModel):
    ok: bool
    sent: int = 0
    detail: str | None = None


class SubscriptionLimitError(Exception):
    """The server already holds as many subscriptions as it allows."""


# --- keys --------------------------------------------------------------------


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def generate_keys() -> tuple[str, str]:
    """A new VAPID key pair: (public key for the browser, private key), base64url."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    public = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return _b64(public), _b64(raw)


def contact(settings: Settings) -> str:
    """The VAPID "sub" claim: how a push service can reach the sender (an email)."""
    candidate = settings.contact_email.strip()
    if candidate and "@" in candidate and " " not in candidate:
        return f"mailto:{candidate}"
    return "mailto:flightwatch@example.com"


def enabled(settings: Settings) -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


# --- subscriptions -------------------------------------------------------------


def subscribe(
    conn: sqlite3.Connection, body: SubscribeIn, *, max_subs: int = MAX_SUBSCRIPTIONS
) -> None:
    kinds = [k for k in KINDS if k in body.kinds]
    with conn:
        known = conn.execute(
            "SELECT 1 FROM push_subscriptions WHERE endpoint = ?", (body.subscription.endpoint,)
        ).fetchone()
        if not known:
            count = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
            if count >= max_subs:
                raise SubscriptionLimitError
        conn.execute(
            "INSERT INTO push_subscriptions (endpoint, keys_json, kinds, created_at, fail_count) "
            "VALUES (?, ?, ?, ?, 0) ON CONFLICT (endpoint) DO UPDATE SET "
            "keys_json = excluded.keys_json, kinds = excluded.kinds, fail_count = 0",
            (
                body.subscription.endpoint,
                body.subscription.keys.model_dump_json(),
                json.dumps(kinds),
                utcnow(),
            ),
        )


def prefs(conn: sqlite3.Connection, endpoint: str) -> AlertPrefs | None:
    row = conn.execute(
        "SELECT kinds FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
    ).fetchone()
    return None if row is None else AlertPrefs(kinds=json.loads(row["kinds"]))


def unsubscribe(conn: sqlite3.Connection, endpoint: str) -> bool:
    with conn:
        return (
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).rowcount
            > 0
        )


@dataclass
class Sub:
    endpoint: str
    keys: dict[str, str]
    kinds: list[str]


def subscriptions(conn: sqlite3.Connection, kind: str | None = None) -> list[Sub]:
    subs = [
        Sub(r["endpoint"], json.loads(r["keys_json"]), json.loads(r["kinds"]))
        for r in conn.execute("SELECT * FROM push_subscriptions ORDER BY created_at")
    ]
    return [s for s in subs if kind is None or kind in s.kinds]


# --- sending -------------------------------------------------------------------

# (subscription info, JSON payload, VAPID private key, claims) -> HTTP status
Sender = Callable[[Mapping[str, Any], str, str, dict[str, str]], int]


def webpush_sender(info: Mapping[str, Any], data: str, key: str, claims: dict[str, str]) -> int:
    from pywebpush import WebPushException, webpush

    try:
        resp = webpush(
            subscription_info=dict(info),
            data=data,
            vapid_private_key=key,
            vapid_claims=dict(claims),
            ttl=TTL_SECONDS,
            timeout=10,
        )
    except WebPushException as e:
        return e.response.status_code if e.response is not None else 0
    return int(getattr(resp, "status_code", 201))


def send(
    conn: sqlite3.Connection,
    settings: Settings,
    sub: Sub,
    payload: Mapping[str, str],
    sender: Sender = webpush_sender,
) -> bool:
    """Send one notification. Gone subscriptions are removed; repeated failures too."""
    claims = {"sub": contact(settings)}
    info = {"endpoint": sub.endpoint, "keys": sub.keys}
    try:
        status = sender(info, json.dumps(payload), settings.vapid_private_key, claims)
    except Exception as e:  # a push problem must never break a run
        log.warning("push to %s failed: %s", sub.endpoint[:40], e)
        status = 0
    with conn:
        if 200 <= status < 300:
            conn.execute(
                "UPDATE push_subscriptions SET last_ok_at = ?, fail_count = 0 WHERE endpoint = ?",
                (utcnow(), sub.endpoint),
            )
            return True
        if status in (404, 410):  # the browser unsubscribed or the subscription expired
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (sub.endpoint,))
            return False
        conn.execute(
            "UPDATE push_subscriptions SET fail_count = fail_count + 1 WHERE endpoint = ?",
            (sub.endpoint,),
        )
        conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ? AND fail_count >= ?",
            (sub.endpoint, MAX_FAILURES),
        )
    return False


def send_kind(
    conn: sqlite3.Connection,
    settings: Settings,
    kind: str,
    title: str,
    body: str,
    url: str | None,
    sender: Sender = webpush_sender,
) -> tuple[int, int]:
    """Send one alert to every device that wants this kind. Returns (sent, failed)."""
    payload = {"title": title, "body": body, "url": url or "/", "tag": f"{kind}-{title}"[:120]}
    sent = failed = 0
    for sub in subscriptions(conn, kind):
        if send(conn, settings, sub, payload, sender):
            sent += 1
        else:
            failed += 1
    return sent, failed
