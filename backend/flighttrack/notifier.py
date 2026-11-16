"""Alerts: a record first, a delivery second.

`Notifier.send` always writes a row to `notifications` (what the app shows), then
hands the message to each channel. A channel failing is logged and reported back; it
never loses the row and never raises. Callers that keep their own record of what was
sent (`alerts_sent`, `deal_posts.alerted_at`, `finds.notified_cents`) pass it as
`record=`, and it is written in the same transaction as the row.

Channels (M8): browser push to the owner's devices (`push.py`) when VAPID keys are
set, each device getting only the kinds it chose; otherwise a log line, so alerts are
recorded and shown in the app either way. The old `file` and `smtp` channels and the
`notify:` tunables section are gone.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from . import push
from .settings import Settings, get_settings
from .store.db import utcnow

log = logging.getLogger(__name__)

KINDS = ("price", "deal", "health")


class NotifyError(Exception):
    """A channel failed to deliver. The notification row is kept regardless."""


class Channel(Protocol):
    name: str

    def send(
        self, conn: sqlite3.Connection, kind: str, title: str, body: str, url: str | None
    ) -> None: ...


@dataclass
class LogChannel:
    """Push is not set up: say so in the log. The app shows the alert either way."""

    name: str = "log"

    def send(
        self, conn: sqlite3.Connection, kind: str, title: str, body: str, url: str | None
    ) -> None:
        log.info("alert (%s, not pushed: push is not set up): %s", kind, title)


@dataclass
class PushChannel:
    """Browser push to every subscribed device that wants this kind."""

    settings: Settings
    sender: push.Sender = push.webpush_sender
    name: str = "push"

    def send(
        self, conn: sqlite3.Connection, kind: str, title: str, body: str, url: str | None
    ) -> None:
        sent, failed = push.send_kind(conn, self.settings, kind, title, body, url, self.sender)
        if failed and not sent:
            raise NotifyError(f"push failed for {failed} device(s)")


@dataclass
class Delivery:
    """What `send` did: the row it wrote, and any channel that failed."""

    id: int
    errors: list[str] = field(default_factory=list)

    @property
    def error(self) -> str | None:
        return "; ".join(self.errors) or None


class Notifier:
    def __init__(self, channels: Sequence[Channel] = ()) -> None:
        self.channels = list(channels)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Notifier:
        """Push when the VAPID keys are set, else a log line."""
        settings = settings or get_settings()
        return cls([PushChannel(settings)] if push.enabled(settings) else [LogChannel()])

    def send(
        self,
        conn: sqlite3.Connection,
        kind: str,
        title: str,
        body: str,
        url: str | None = None,
        *,
        record: Callable[[sqlite3.Connection], None] | None = None,
    ) -> Delivery:
        """Write the notification row (and `record`, in the same transaction), then
        deliver it on every channel. Never raises because of a channel."""
        if kind not in KINDS:
            raise ValueError(f"notification kind must be one of {KINDS}, not {kind!r}")
        conn.commit()
        with conn:
            cur = conn.execute(
                "INSERT INTO notifications (kind, title, body, url, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, title, body, url, utcnow()),
            )
            if record is not None:
                record(conn)
        delivery = Delivery(id=int(cur.lastrowid or 0))
        for ch in self.channels:
            try:
                ch.send(conn, kind, title, body, url)
            except Exception as exc:
                msg = (
                    str(exc)
                    if isinstance(exc, NotifyError)
                    else f"{ch.name} channel failed: {type(exc).__name__}: {exc}"
                )
                delivery.errors.append(msg)
                log.warning("notification %d (%s) not delivered: %s", delivery.id, kind, msg)
        return delivery


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    """The newest notifications, newest first."""
    return conn.execute(
        "SELECT * FROM notifications ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
