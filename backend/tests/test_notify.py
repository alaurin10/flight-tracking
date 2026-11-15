"""The notify seam. Channels must be swappable without touching alert logic,
and nothing a channel does may crash the stage that called it."""

from dataclasses import replace
from email import message_from_bytes, policy

import pytest

from flighttrack import alert, deals, health, notify as notify_mod
from flighttrack.config import Alerts, Deals, DealWatch
from flighttrack.notify import NotifyError, notify
from helpers import add_obs, first_query_id, make_config, make_db

# The subjects the real callers send (alert.run, deals.alert, health.run).
SUBJECTS = [
    "✈ $612 Tokyo · Apr 10",
    "✈ deal: Alaska Air: Seattle – San Diego (and vice versa). $127 (Basic Economy) / $237 (Regular Economy)",
    "⚠ flighttrack is not collecting prices",
]


def test_none_channel_is_a_noop():
    notify({"channel": "none"}, "s", "b")  # must not raise


def test_unknown_channel_raises():
    with pytest.raises(NotifyError, match="unknown notify channel"):
        notify({"channel": "smoke-signal"}, "s", "b")


def test_ntfy_is_gone():
    with pytest.raises(NotifyError, match="unknown notify channel 'ntfy'"):
        notify({"channel": "ntfy", "ntfy": {"topic": "abc"}}, "s", "b")


def test_file_channel_appends(tmp_path):
    path = tmp_path / "digest.md"
    conf = {"channel": "file", "file": {"path": str(path)}}
    notify(conf, "First", "body one", "https://example.test/a")
    notify(conf, "Second", "body two")

    text = path.read_text()
    assert "First" in text and "Second" in text
    assert "https://example.test/a" in text


@pytest.mark.parametrize("subject", SUBJECTS)
def test_file_channel_keeps_real_subjects(tmp_path, subject):
    path = tmp_path / "digest.md"
    notify({"channel": "file", "file": {"path": str(path)}}, subject, "SEA→HND $612")
    assert f"## {subject}\n" in path.read_text(encoding="utf-8")


class FakeSMTP:
    """Stands in for smtplib.SMTP; keeps the message exactly as it would go on the wire."""

    sent: list[bytes] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg.as_bytes())


@pytest.mark.parametrize("subject", SUBJECTS)
def test_smtp_encodes_non_ascii_subjects(monkeypatch, subject):
    FakeSMTP.sent = []
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    notify({"channel": "smtp", "smtp": {"host": "mail.example", "to": "me@example.test"}},
           subject, "SEA→HND $612", "https://google.test/flights?tfs=xyz")

    wire = FakeSMTP.sent[0]
    headers = wire.split(b"\n\n", 1)[0]
    headers.decode("ascii")                    # RFC 5322: header bytes are ASCII
    assert b"=?utf-8?" in headers.lower()      # the subject went out RFC 2047-encoded
    parsed = message_from_bytes(wire, policy=policy.default)
    assert parsed["Subject"] == subject
    assert "SEA→HND $612" in parsed.get_content() and "https://google.test/flights?tfs=xyz" in parsed.get_content()


def test_smtp_requires_host_and_recipient():
    with pytest.raises(NotifyError, match="host and to"):
        notify({"channel": "smtp", "smtp": {"port": 587}}, "s", "b")


def test_any_channel_exception_becomes_notify_error(monkeypatch):
    def boom(*a):
        raise ValueError("bad header")

    monkeypatch.setitem(notify_mod.CHANNELS, "file", boom)
    with pytest.raises(NotifyError, match="file channel failed: ValueError: bad header"):
        notify({"channel": "file"}, "s", "b")


# ---------------------------------------------------------------------------
# Callers: a channel that raises must never become an exception in the stage
# ---------------------------------------------------------------------------

@pytest.fixture
def broken_file_channel(monkeypatch):
    def boom(*a):
        raise ValueError("synthetic channel failure")

    monkeypatch.setitem(notify_mod.CHANNELS, "file", boom)
    return {"channel": "file", "file": {"path": "unused"}}


def test_alert_run_reports_delivery_error(tmp_path, broken_file_channel):
    cfg = make_config(alerts=Alerts(min_observations_for_all_time_low=99), notify=broken_file_channel)
    conn, cfg = make_db(tmp_path, cfg)
    add_obs(conn, first_query_id(conn, "SLC"), 13000)          # under the $140 SLC target

    stats = alert.run(conn, cfg, verbose=False)
    assert stats.qualified == 1 and stats.sent == 0
    assert "synthetic channel failure" in stats.delivery_error
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0


def test_deals_alert_reports_delivery_error(tmp_path, broken_file_channel):
    watch = DealWatch("Anything", origins=("Seattle",), destinations=())
    cfg = replace(make_config(notify=broken_file_channel), deals=Deals(enabled=True, feeds=("f",), watches=(watch,)))
    conn, cfg = make_db(tmp_path, cfg)
    conn.execute(
        "INSERT INTO deal_posts (guid, feed, title, seen_at, price_cents, watch) VALUES ('g', 'f', ?, ?, 12700, 'Anything')",
        ("Seattle – San Diego $127", "2026-11-14T00:00:00Z"),
    )
    conn.commit()

    from datetime import datetime, timezone

    stats = deals.alert(conn, cfg, verbose=False, now=datetime(2026, 11, 14, 12, tzinfo=timezone.utc))
    assert "synthetic channel failure" in stats.delivery_error
    assert conn.execute("SELECT alerted_at FROM deal_posts").fetchone()[0] is None


def test_health_run_returns_false(tmp_path, broken_file_channel):
    from datetime import datetime, timedelta, timezone

    from flighttrack.db import set_state

    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    conn, cfg = make_db(tmp_path, make_config(notify=broken_file_channel))
    set_state(conn, "last_success_at", (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))

    rep, notified = health.run(conn, cfg, verbose=False, now=now)
    assert not rep.ok and notified is False
