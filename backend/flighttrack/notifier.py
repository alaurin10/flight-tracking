"""Alerts: a record first, a delivery second.

`Notifier.send` always writes a row to `notifications` (what the app shows), then
hands the message to each configured channel. A channel failing is logged and
reported back; it never loses the row and never raises. Callers that keep their
own record of what was sent (`alerts_sent`, `deal_posts.alerted_at`) pass it as
`record=`, and it is written in the same transaction as the row.

Channels for now: `file` (append to a Markdown digest) and `smtp`, chosen by
`notify.channel` in config/flighttrack.yaml. Browser push replaces both in M8.

Stdlib only.
"""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

from .config import Config
from .store.db import utcnow

log = logging.getLogger(__name__)

KINDS = ("price", "deal", "health")


class NotifyError(Exception):
    """A channel failed to deliver. The notification row is kept regardless."""


class Channel(Protocol):
    name: str

    def send(self, title: str, body: str, url: str | None) -> None: ...


@dataclass
class FileChannel:
    """Append each message to a Markdown digest file."""

    path: Path
    name: str = "file"

    def send(self, title: str, body: str, url: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = f"\n## {title}\n\n_{utcnow()}_\n\n{body}\n"
        if url:
            entry += f"\n[Open]({url})\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry)


@dataclass
class SmtpChannel:
    host: str
    to: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    name: str = "smtp"

    def send(self, title: str, body: str, url: str | None) -> None:
        # EmailMessage's default policy encodes a non-ASCII Subject (✈, ⚠) as
        # RFC 2047 when the message is serialized.
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = self.username or f"flighttrack@{self.host}"
        msg["To"] = self.to
        msg.set_content(body + (f"\n\n{url}\n" if url else "\n"))
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                s.starttls()
                if self.username and self.password:
                    s.login(self.username, self.password)
                s.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifyError(f"SMTP send failed: {exc}") from exc


def channels_from_config(conf: dict[str, Any] | None) -> list[Channel]:
    """The channels `notify:` names. `none` (or no section) means record only."""
    conf = conf or {}
    channel = conf.get("channel", "none")
    if channel == "none":
        return []
    if channel == "file":
        file_conf = conf.get("file") or {}
        return [FileChannel(Path(str(file_conf.get("path") or "out/digest.md")))]
    if channel == "smtp":
        smtp = conf.get("smtp") or {}
        if not smtp.get("host") or not smtp.get("to"):
            raise NotifyError("notify.smtp needs at least host and to")
        return [
            SmtpChannel(
                host=str(smtp["host"]),
                to=str(smtp["to"]),
                port=int(smtp.get("port", 587)),
                username=smtp.get("username") or None,
                password=smtp.get("password") or None,
            )
        ]
    raise NotifyError(f"unknown notify channel {channel!r}")


@dataclass
class BrokenChannel:
    """A channel the config names but that cannot work: every send says why."""

    error: str
    name: str = "config"

    def send(self, title: str, body: str, url: str | None) -> None:
        raise NotifyError(self.error)


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
    def from_config(cls, cfg: Config) -> Notifier:
        """The configured channels. A broken `notify:` section never stops alerts from
        being recorded: each send reports the problem as a delivery error instead."""
        try:
            return cls(channels_from_config(cfg.notify))
        except NotifyError as exc:
            return cls([BrokenChannel(str(exc))])

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
                ch.send(title, body, url)
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
