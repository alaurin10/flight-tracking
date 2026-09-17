"""Delivery, behind exactly one function.

The whole point of this module is that `alert.py` never learns how a message
gets to a phone. Swapping ntfy for SMTP is a one-line config change, and the
alert logic is untouched.

Stdlib only — ntfy is a single HTTP POST, which does not justify a dependency.
"""

from __future__ import annotations

import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .db import utcnow


class NotifyError(Exception):
    """Delivery failed. Never fatal to a fetch — history matters more."""


def _post_ntfy(conf: dict[str, Any], subject: str, body: str, url: str | None) -> None:
    server = (conf.get("server") or "https://ntfy.sh").rstrip("/")
    topic = conf.get("topic")
    if not topic:
        raise NotifyError("notify.ntfy.topic is not set (check $NTFY_TOPIC)")

    req = urllib.request.Request(
        f"{server}/{topic}",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Title": subject,
            "Tags": "airplane",
            "Content-Type": "text/plain; charset=utf-8",
            **({"Click": url} if url else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status >= 300:
                raise NotifyError(f"ntfy returned HTTP {resp.status}")
    except urllib.error.URLError as exc:
        raise NotifyError(f"ntfy POST failed: {exc}") from exc


def _send_smtp(conf: dict[str, Any], subject: str, body: str, url: str | None) -> None:
    host, to = conf.get("host"), conf.get("to")
    if not host or not to:
        raise NotifyError("notify.smtp needs at least host and to")

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


def notify(config_notify: dict[str, Any], subject: str, body: str, url: str | None = None) -> None:
    """Deliver one message over whichever channel is configured."""
    channel = (config_notify or {}).get("channel", "none")
    if channel == "none":
        return
    if channel == "ntfy":
        _post_ntfy(config_notify.get("ntfy") or {}, subject, body, url)
    elif channel == "smtp":
        _send_smtp(config_notify.get("smtp") or {}, subject, body, url)
    elif channel == "file":
        _append_file(config_notify.get("file") or {}, subject, body, url)
    else:
        raise NotifyError(f"unknown notify channel {channel!r}")
