"""Delivery, behind exactly one function.

The whole point of this module is that `alert.py` never learns how a message
gets to a person. Swapping the digest file for SMTP is a one-line config
change, and the alert logic is untouched.

Channels: `file` (append to a Markdown digest), `smtp`, and `none`. Browser
push replaces all of them in the web app (see docs/PLAN.md, M8).

Stdlib only.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .db import utcnow


class NotifyError(Exception):
    """Delivery failed. Never fatal to a fetch — history matters more."""


def _send_smtp(conf: dict[str, Any], subject: str, body: str, url: str | None) -> None:
    host, to = conf.get("host"), conf.get("to")
    if not host or not to:
        raise NotifyError("notify.smtp needs at least host and to")

    # EmailMessage's default policy encodes a non-ASCII Subject (✈, ⚠) as
    # RFC 2047 when the message is serialized.
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = conf.get("username") or f"flighttrack@{host}"
    msg["To"] = to
    msg.set_content(body + (f"\n\n{url}\n" if url else "\n"))

    try:
        with smtplib.SMTP(host, int(conf.get("port", 587)), timeout=30) as s:
            s.starttls()
            if conf.get("username") and conf.get("password"):
                s.login(conf["username"], conf["password"])
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise NotifyError(f"SMTP send failed: {exc}") from exc


def _append_file(conf: dict[str, Any], subject: str, body: str, url: str | None) -> None:
    path = Path(conf.get("path") or "out/digest.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = f"\n## {subject}\n\n_{utcnow()}_\n\n{body}\n"
    if url:
        entry += f"\n[Open search]({url})\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)


CHANNELS = {"smtp": _send_smtp, "file": _append_file}


def notify(config_notify: dict[str, Any], subject: str, body: str, url: str | None = None) -> None:
    """Deliver one message over whichever channel is configured.

    Raises only `NotifyError`. Whatever goes wrong inside a channel (an
    encoding problem, a full disk, a bug) is turned into one, because every
    caller catches exactly that and must never crash on delivery.
    """
    channel = (config_notify or {}).get("channel", "none")
    if channel == "none":
        return
    send = CHANNELS.get(channel)
    if send is None:
        raise NotifyError(f"unknown notify channel {channel!r}")
    try:
        send(config_notify.get(channel) or {}, subject, body, url)
    except NotifyError:
        raise
    except Exception as exc:
        raise NotifyError(f"{channel} channel failed: {type(exc).__name__}: {exc}") from exc
