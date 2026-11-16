"""The notifier: every alert is a record first and a delivery second. Channels must
be swappable without touching alert logic, nothing a channel does may crash the
stage that called it, and a failing channel never loses the notification row."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_obs, first_query_id, make_config, make_db

from flighttrack import alert, deals, health, push
from flighttrack.config import Alerts, Deals, DealWatch, Trip
from flighttrack.notifier import LogChannel, Notifier, PushChannel, recent
from flighttrack.settings import Settings
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

    def send(self, conn, kind, title, body, url):
        raise ValueError("synthetic channel failure")


class Keep:
    name = "keep"

    def __init__(self):
        self.sent = []

    def send(self, conn, kind, title, body, url):
        self.sent.append((title, body, url))


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


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


# ---------------------------------------------------------------------------
# Push (M8): to each device that wants the kind; a gone device is dropped
# ---------------------------------------------------------------------------

KEYS = {"vapid_public_key": "pub", "vapid_private_key": "priv", "contact_email": "me@example.test"}


def device(conn, name, kinds):
    push.subscribe(
        conn,
        push.SubscribeIn(
            subscription={
                "endpoint": f"https://fcm.googleapis.com/fcm/send/{name}",
                "keys": {"p256dh": "p", "auth": "a"},
            },
            kinds=kinds,
        ),
    )


class Sender:
    """Stands in for pywebpush: records each push and answers with a status per device."""

    def __init__(self, status=None):
        self.sent = []
        self.status = status or {}

    def __call__(self, info, data, key, claims):
        self.sent.append((info["endpoint"].rsplit("/", 1)[1], data, key, claims))
        return self.status.get(info["endpoint"].rsplit("/", 1)[1], 201)


def test_push_goes_only_to_devices_that_want_the_kind(tmp_path):
    conn, _ = make_db(tmp_path)
    device(conn, "phone", ["price", "deal", "health"])
    device(conn, "laptop", ["health"])
    sender = Sender()
    n = Notifier([PushChannel(Settings(**KEYS), sender)])
    d = n.send(conn, "price", "✈ $612 Tokyo · Apr 10", "body", "/trip/japan")
    assert d.error is None and [s[0] for s in sender.sent] == ["phone"]
    _, data, key, claims = sender.sent[0]
    assert '"url": "/trip/japan"' in data and key == "priv"
    assert claims == {"sub": "mailto:me@example.test"}
    n.send(conn, "health", "⚠ Flight Watch has stopped collecting prices", "body", "/status")
    assert [s[0] for s in sender.sent[1:]] == ["phone", "laptop"]


def test_a_gone_device_is_dropped_and_a_failed_push_keeps_the_row_and_record(tmp_path):
    conn, _ = make_db(tmp_path)
    device(conn, "old", ["deal"])
    device(conn, "flaky", ["deal"])
    sender = Sender({"old": 410, "flaky": 500})
    d = Notifier([PushChannel(Settings(**KEYS), sender)]).send(
        conn, "deal", "T", "B", "/deals",
        record=lambda c: c.execute("INSERT INTO state VALUES ('rec', 'yes')"),
    )  # fmt: skip
    assert d.error == "push failed for 2 device(s)"
    assert count(conn, "notifications") == 1
    assert conn.execute("SELECT value FROM state WHERE key = 'rec'").fetchone()[0] == "yes"
    left = [s.endpoint.rsplit("/", 1)[1] for s in push.subscriptions(conn)]
    assert left == ["flaky"]  # 410: gone; 500: kept, one failure counted
    for _ in range(push.MAX_FAILURES):
        push.send_kind(conn, Settings(**KEYS), "deal", "T", "B", "/deals", sender)
    assert push.subscriptions(conn) == []  # five failures in a row: dropped


def test_push_is_used_only_when_the_keys_are_set():
    assert isinstance(Notifier.from_settings(Settings()).channels[0], LogChannel)
    assert isinstance(Notifier.from_settings(Settings(**KEYS)).channels[0], PushChannel)


def test_only_browser_push_services_and_at_most_20_devices(tmp_path):
    conn, _ = make_db(tmp_path)
    assert push.is_push_service("https://web.push.apple.com/abc")
    assert push.is_push_service("https://wns2-bl2p.notify.windows.com/w/?token=x")
    for bad in ("http://fcm.googleapis.com/x", "https://evil.example/x",
                "https://fcm.googleapis.com:8443/x", "https://u@fcm.googleapis.com/x"):  # fmt: skip
        assert not push.is_push_service(bad)
    for i in range(push.MAX_SUBSCRIPTIONS):
        device(conn, f"d{i}", ["health"])
    with pytest.raises(push.SubscriptionLimitError):
        device(conn, "one-too-many", ["health"])
    device(conn, "d0", ["price"])  # an existing device may change its choice
    assert push.prefs(conn, "https://fcm.googleapis.com/fcm/send/d0").kinds == ["price"]


def test_a_price_alert_opens_its_trips_page(tmp_path):
    from datetime import date

    depart = (date.today() + timedelta(days=40)).isoformat()
    trip = Trip("japan", "HND", depart, None, target_price=80000)
    cfg = make_config(trips=[trip], alerts=Alerts(min_observations_for_all_time_low=99))
    conn, cfg = make_db(tmp_path, cfg)
    qid = conn.execute("SELECT query_id FROM watch_queries WHERE trip_id IS NOT NULL").fetchone()[0]
    add_obs(conn, qid, 70000)
    keep = Keep()
    alert.run(conn, cfg, verbose=False, notifier=Notifier([keep]))
    (_title, body, url) = keep.sent[0]
    assert url == "/trip/japan" and body.startswith("Tokyo")


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
