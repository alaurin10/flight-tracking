"""The notifier: every alert is a record first and a delivery second. Channels must
be swappable without touching alert logic, nothing a channel does may crash the
stage that called it, and a failing channel never loses the notification row."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from typing import ClassVar

import pytest
from helpers import add_obs, first_query_id, make_config, make_db

from flighttrack import alert, deals, health
from flighttrack.config import Alerts, Deals, DealWatch
from flighttrack.notifier import (
    FileChannel,
    Notifier,
    NotifyError,
    SmtpChannel,
    channels_from_config,
    recent,
)
from flighttrack.store.db import set_state

# The subjects the real callers send (alert.run, deals.alert, health.run).
SUBJECTS = [
    "✈ $612 Tokyo · Apr 10",
    "✈ deal: Alaska Air: Seattle – San Diego (and vice versa). $127 (Basic Economy) / $237 "
    "(Regular Economy)",
    "⚠ Flight Watch has stopped collecting prices",
]


class Boom:
    """A channel that always raises something other than NotifyError."""

    name = "boom"

    def send(self, title, body, url):
        raise ValueError("synthetic channel failure")


class Keep:
    name = "keep"

    def __init__(self):
        self.sent = []

    def send(self, title, body, url):
        self.sent.append((title, body, url))


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_none_channel_means_record_only():
    assert channels_from_config({"channel": "none"}) == []
    assert channels_from_config(None) == []


def test_unknown_channel_raises():
    with pytest.raises(NotifyError, match="unknown notify channel"):
        channels_from_config({"channel": "smoke-signal"})


def test_ntfy_is_gone():
    with pytest.raises(NotifyError, match="unknown notify channel 'ntfy'"):
        channels_from_config({"channel": "ntfy", "ntfy": {"topic": "abc"}})


def test_smtp_requires_host_and_recipient():
    with pytest.raises(NotifyError, match="host and to"):
        channels_from_config({"channel": "smtp", "smtp": {"port": 587}})


def test_send_writes_the_row_then_delivers(tmp_path):
    conn, _ = make_db(tmp_path)
    keep = Keep()
    d = Notifier([keep]).send(conn, "price", "T", "B", "https://example.test/a")
    row = recent(conn)[0]
    assert (row["id"], row["kind"], row["title"], row["body"], row["url"]) == (
        d.id,
        "price",
        "T",
        "B",
        "https://example.test/a",
    )
    assert keep.sent == [("T", "B", "https://example.test/a")] and d.error is None


def test_a_raising_channel_keeps_the_row(tmp_path):
    """Acceptance (M4): a notifier channel that raises still leaves the notification row."""
    conn, _ = make_db(tmp_path)
    keep = Keep()
    d = Notifier([Boom(), keep]).send(conn, "health", "T", "B")
    assert count(conn, "notifications") == 1
    assert "boom channel failed: ValueError: synthetic channel failure" in d.error
    assert keep.sent  # the next channel still gets it


def test_record_is_written_with_the_row(tmp_path):
    conn, _ = make_db(tmp_path)
    Notifier([Boom()]).send(
        conn, "deal", "T", "B", record=lambda c: c.execute("INSERT INTO state VALUES ('k', 'v')")
    )
    assert conn.execute("SELECT value FROM state WHERE key = 'k'").fetchone()[0] == "v"


def test_unknown_kind_is_refused(tmp_path):
    conn, _ = make_db(tmp_path)
    with pytest.raises(ValueError, match="kind"):
        Notifier().send(conn, "gossip", "T", "B")
    assert count(conn, "notifications") == 0


def test_file_channel_appends(tmp_path):
    ch = FileChannel(tmp_path / "digest.md")
    ch.send("First", "body one", "https://example.test/a")
    ch.send("Second", "body two", None)
    text = (tmp_path / "digest.md").read_text()
    assert "First" in text and "Second" in text and "https://example.test/a" in text


@pytest.mark.parametrize("subject", SUBJECTS)
def test_file_channel_keeps_real_subjects(tmp_path, subject):
    path = tmp_path / "digest.md"
    FileChannel(path).send(subject, "SEA→HND $612", None)
    assert f"## {subject}\n" in path.read_text(encoding="utf-8")


def test_file_channel_from_config(tmp_path):
    (ch,) = channels_from_config({"channel": "file", "file": {"path": str(tmp_path / "d.md")}})
    assert isinstance(ch, FileChannel) and ch.path == tmp_path / "d.md"


class FakeSMTP:
    """Stands in for smtplib.SMTP; keeps the message exactly as it would go on the wire."""

    sent: ClassVar[list[bytes]] = []

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
    (ch,) = channels_from_config(
        {"channel": "smtp", "smtp": {"host": "mail.example", "to": "me@example.test"}}
    )
    assert isinstance(ch, SmtpChannel)
    ch.send(subject, "SEA→HND $612", "https://google.test/flights?tfs=xyz")

    wire = FakeSMTP.sent[0]
    headers = wire.split(b"\n\n", 1)[0]
    headers.decode("ascii")  # RFC 5322: header bytes are ASCII
    assert b"=?utf-8?" in headers.lower()  # the subject went out RFC 2047-encoded
    parsed = message_from_bytes(wire, policy=policy.default)
    assert parsed["Subject"] == subject
    content = parsed.get_content()
    assert "SEA→HND $612" in content and "https://google.test/flights?tfs=xyz" in content


# ---------------------------------------------------------------------------
# Callers: a channel that raises never becomes an exception, and the alert is kept
# ---------------------------------------------------------------------------


def test_alert_run_records_and_reports_delivery_error(tmp_path):
    cfg = make_config(alerts=Alerts(min_observations_for_all_time_low=99))
    conn, cfg = make_db(tmp_path, cfg)
    add_obs(conn, first_query_id(conn, "SLC"), 13000)  # under the $140 SLC target

    stats = alert.run(conn, cfg, verbose=False, notifier=Notifier([Boom()]))
    assert stats.qualified == 1 and stats.sent == 1
    assert "synthetic channel failure" in stats.delivery_error
    # Recorded once, in both places, so the dedup window holds it.
    assert count(conn, "alerts_sent") == 1
    (row,) = recent(conn)
    assert row["kind"] == "price" and row["title"].startswith("✈ $130")
    assert alert.run(conn, cfg, verbose=False, notifier=Notifier([Boom()])).sent == 0


def test_alert_digest_is_one_notification(tmp_path):
    cfg = make_config(alerts=Alerts(min_observations_for_all_time_low=99, max_per_run=1))
    conn, cfg = make_db(tmp_path, cfg)
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT q.id FROM queries q JOIN routes r ON r.id = q.route_id "
            "WHERE r.destination = 'SLC' LIMIT 2"
        )
    ]
    for qid in ids:
        add_obs(conn, qid, 13000)
    stats = alert.run(conn, cfg, verbose=False, notifier=Notifier())
    assert stats.sent == 1 and stats.digested == 2
    assert count(conn, "notifications") == 1 and count(conn, "alerts_sent") == 2


def test_deals_alert_records_and_reports_delivery_error(tmp_path):
    watch = DealWatch("Anything", origins=("Seattle",), destinations=())
    cfg = replace(make_config(), deals=Deals(enabled=True, feeds=("f",)), deal_watches=[watch])
    conn, cfg = make_db(tmp_path, cfg)
    conn.execute(
        "INSERT INTO deal_posts (guid, feed, title, seen_at, price_cents, watch) VALUES ('g', "
        "'f', ?, ?, 12700, 'Anything')",
        ("Seattle – San Diego $127", "2026-11-14T00:00:00Z"),
    )
    conn.commit()

    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    stats = deals.alert(conn, cfg, verbose=False, now=now, notifier=Notifier([Boom()]))
    assert "synthetic channel failure" in stats.delivery_error
    assert conn.execute("SELECT alerted_at FROM deal_posts").fetchone()[0] is not None
    assert recent(conn)[0]["kind"] == "deal"


def test_health_records_the_warning_and_then_the_recovery(tmp_path):
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    conn, cfg = make_db(tmp_path)
    set_state(conn, "last_success_at", (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))

    rep, notified = health.run(conn, cfg, verbose=False, now=now, notifier=Notifier([Boom()]))
    assert not rep.ok and notified is True
    assert recent(conn)[0]["title"] == health.TITLE_DOWN
    # Not again within renotify_hours.
    assert health.run(conn, cfg, verbose=False, now=now, notifier=Notifier())[1] is False

    set_state(conn, "last_success_at", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    rep, notified = health.run(conn, cfg, verbose=False, now=now, notifier=Notifier())
    assert rep.ok and notified is True
    assert recent(conn)[0]["title"] == health.TITLE_UP
    # Recovery is said once.
    assert health.run(conn, cfg, verbose=False, now=now, notifier=Notifier())[1] is False
    assert [r["kind"] for r in recent(conn)] == ["health", "health"]
