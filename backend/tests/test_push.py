"""Push alert subscriptions over the API (M8): owner only, real push services only, at
most 20 devices, per-kind choices, a test push through a fake sender, and "not set up"
without VAPID keys. Offline: the sender is replaced on app.state."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from flighttrack import push
from flighttrack.api.app import create_app
from flighttrack.settings import Settings
from flighttrack.store.db import open_db

PASSWORD = "correct horse battery staple"
SAME_SITE = {"Origin": "http://testserver"}
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"
SUB = {"endpoint": ENDPOINT, "keys": {"p256dh": "p", "auth": "a"}}


def make(monkeypatch: pytest.MonkeyPatch, keys: bool = True) -> TestClient:
    monkeypatch.setenv("OWNER_PASSWORD", PASSWORD)
    if keys:
        monkeypatch.setenv("VAPID_PUBLIC_KEY", "public-key")
        monkeypatch.setenv("VAPID_PRIVATE_KEY", "private-key")
    settings = Settings()
    open_db(settings.database).close()
    return TestClient(create_app(settings))


@pytest.fixture
def c(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with make(monkeypatch) as client:
        sent: list[tuple[str, str]] = []

        def sender(info, data, key, claims):
            sent.append((info["endpoint"], data))
            return 201

        client.app.state.push_sender = sender  # type: ignore[attr-defined]
        client.sent = sent  # type: ignore[attr-defined]
        yield client


def sign_in(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers=SAME_SITE)
    assert r.status_code == 200, r.text


def test_only_the_owner_subscribes_and_only_from_this_site(c: TestClient) -> None:
    assert c.get("/api/push/key").status_code == 401
    r = c.post("/api/push/subscribe", json={"subscription": SUB}, headers=SAME_SITE)
    assert r.status_code == 401
    sign_in(c)
    r = c.post(
        "/api/push/subscribe",
        json={"subscription": SUB},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert c.get("/api/push/key").json() == {"key": "public-key"}


def test_subscribe_choose_kinds_test_and_turn_off(c: TestClient) -> None:
    sign_in(c)
    r = c.post(
        "/api/push/subscribe",
        json={"subscription": SUB, "kinds": ["deal", "health"]},
        headers=SAME_SITE,
    )
    assert r.json() == {"ok": True, "sent": 0, "detail": None}
    r = c.post("/api/push/prefs", json={"endpoint": ENDPOINT}, headers=SAME_SITE)
    assert r.json() == {"kinds": ["deal", "health"]}
    r = c.post("/api/push/test", json={"endpoint": ENDPOINT}, headers=SAME_SITE)
    assert r.json()["ok"] is True and r.json()["sent"] == 1
    endpoint, data = c.sent[0]  # type: ignore[attr-defined]
    assert endpoint == ENDPOINT and "Flight Watch alerts are on" in data
    r = c.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT}, headers=SAME_SITE)
    assert r.json()["ok"] is True
    r = c.post("/api/push/prefs", json={"endpoint": ENDPOINT}, headers=SAME_SITE)
    assert r.status_code == 404


def test_only_real_push_services_and_at_most_20_devices(c: TestClient) -> None:
    sign_in(c)
    bad = {"endpoint": "https://evil.example/hook", "keys": {"p256dh": "p", "auth": "a"}}
    r = c.post("/api/push/subscribe", json={"subscription": bad}, headers=SAME_SITE)
    assert r.status_code == 422
    conn = open_db(c.app.state.settings.database)  # type: ignore[attr-defined]
    for i in range(push.MAX_SUBSCRIPTIONS - 1):  # straight in: saving is rate limited
        push.subscribe(conn, push.SubscribeIn(subscription={**SUB, "endpoint": f"{ENDPOINT}{i}"}))
    last = {**SUB, "endpoint": f"{ENDPOINT}-last"}
    r = c.post("/api/push/subscribe", json={"subscription": last}, headers=SAME_SITE)
    assert r.status_code == 200  # the 20th
    r = c.post("/api/push/subscribe", json={"subscription": SUB}, headers=SAME_SITE)
    assert r.status_code == 422 and "At most 20 devices" in r.json()["detail"]


def test_without_keys_push_is_not_set_up(monkeypatch: pytest.MonkeyPatch) -> None:
    with make(monkeypatch, keys=False) as client:
        assert client.get("/api/meta").json()["push_enabled"] is False
        sign_in(client)
        r = client.get("/api/push/key")
        assert r.status_code == 404 and "not set up" in r.json()["detail"]
