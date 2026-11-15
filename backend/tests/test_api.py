"""The web API with FastAPI's TestClient: every endpoint, sign-in, the Origin check,
the lockout, rate limits, headers, and `/api/health` following the newest price.

Offline: the database is seeded with `demo.seed` (synthetic prices) on a fixed clock.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import FakeTransport

from flighttrack import alert, app_settings, demo, fetch, jobs, watches
from flighttrack import config as config_mod
from flighttrack.api import ratelimit
from flighttrack.api.app import create_app, inline_script_hashes
from flighttrack.cli import api_schema, main
from flighttrack.gflights.transport import TransportError
from flighttrack.settings import REPO_ROOT, Settings
from flighttrack.store.db import iso, open_db, set_clock, set_state

TODAY = date(2027, 1, 10)
NOW = datetime(2027, 1, 10, 12, 0, tzinfo=UTC)  # demo prices are stamped 10:00 each day
PASSWORD = "correct horse battery staple"
SAME_SITE = {"Origin": "http://testserver"}
TRIP = "tokyo-spring"  # the demo's trip and searches
SEARCH = "tokyo-week"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("OWNER_PASSWORD", PASSWORD)
    return Settings()


def seed(settings: Settings) -> None:
    cfg = config_mod.load(strict_secrets=False, settings=settings)
    conn = open_db(settings.database)
    try:
        demo.seed(conn, cfg, days=45, today=TODAY)
    finally:
        conn.close()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    set_clock(lambda: NOW)
    seed(settings)
    with TestClient(create_app(settings)) as c:
        yield c


def sign_in(c: TestClient) -> None:
    r = c.post("/api/auth/login", json={"password": PASSWORD}, headers=SAME_SITE)
    assert r.status_code == 200, r.text
    assert r.json() == {"is_owner": True, "owner_login_enabled": True}


@pytest.fixture
def owner(client: TestClient) -> TestClient:
    sign_in(client)
    return client


def db(settings: Settings):
    return open_db(settings.database)


# ---------------------------------------------------------------------------
# liveness, health, meta, headers
# ---------------------------------------------------------------------------


def test_livez_without_a_database(settings: Settings) -> None:
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/livez").json() == {"ok": True}
        r = c.get("/api/health")
        assert r.status_code == 503 and r.json()["status"] == "no_data"
        assert c.get("/api/overview").status_code == 503  # no database yet
        assert c.get("/").status_code == 404  # no frontend build here: the API only
    assert not settings.database.exists()  # reading never creates it


def test_health_follows_the_age_of_the_newest_price(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["newest_price_at"] == f"{TODAY.isoformat()}T10:00:00Z"
    assert body["age_s"] == 2 * 3600 and body["max_age_s"] == 36 * 3600
    assert r.headers["cache-control"] == "no-store"

    set_clock(lambda: datetime(2027, 1, 11, 22, 0, tzinfo=UTC))  # 36 h exactly: still ok
    assert client.get("/api/health").status_code == 200
    set_clock(lambda: datetime(2027, 1, 11, 22, 1, tzinfo=UTC))
    r = client.get("/api/health")
    assert r.status_code == 503 and r.json()["status"] == "stale"

    later = datetime(2027, 1, 11, 22, 1, tzinfo=UTC)
    conn = db(client.app.state.settings)  # type: ignore[attr-defined]
    qid = conn.execute("SELECT id FROM queries LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO observations (query_id, observed_at, price_cents, is_best, source) "
        "VALUES (?, ?, 50000, 1, 'test')",
        (qid, iso(later - timedelta(hours=1))),
    )
    conn.commit()
    conn.close()
    assert client.get("/api/health").status_code == 200  # a fresh price: healthy again


def test_health_is_503_when_the_worker_heartbeat_goes_stale(
    client: TestClient, settings: Settings
) -> None:
    beat = settings.heartbeat_path
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.touch()
    os.utime(beat, (NOW.timestamp() - 30, NOW.timestamp() - 30))
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["worker_heartbeat_age_s"] == 30
    os.utime(beat, (NOW.timestamp() - 600, NOW.timestamp() - 600))
    r = client.get("/api/health")
    assert r.status_code == 503 and r.json()["status"] == "worker_stopped"


def test_meta(client: TestClient, settings: Settings) -> None:
    body = client.get("/api/meta").json()
    assert body["home_airport"] == "SEA" and body["currency"] == "USD"
    assert body["owner_login_enabled"] is True and body["push_enabled"] is False
    assert body["commit"] is None


def test_security_headers_on_every_response(client: TestClient) -> None:
    for path in ("/api/livez", "/api/overview", "/api/nope", "/", "/robots.txt"):
        h = client.get(path).headers
        csp = h["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in h["permissions-policy"]
        assert h["x-robots-tag"] == "noindex, nofollow"
        assert "strict-transport-security" not in h  # only in production
    assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"


def test_production_adds_hsts_and_hides_the_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "prod")
    with TestClient(create_app(Settings())) as c:
        r = c.get("/api/livez")
        assert r.headers["strict-transport-security"] == "max-age=31536000"
        assert c.get("/api/docs").status_code == 404
        assert c.get("/api/openapi.json").status_code == 404


def test_without_a_build_only_the_api_answers(client: TestClient) -> None:
    assert client.get("/").status_code == 404
    assert client.get("/trip/tokyo").status_code == 404
    csp = client.get("/api/livez").headers["content-security-policy"]
    assert "script-src 'self';" in csp  # no inline script allowed at all


def test_spa_fallback_when_the_frontend_is_built(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    static = tmp_path / "static"
    (static / "_app" / "immutable").mkdir(parents=True)
    (static / "index.html").write_text(
        "<!doctype html><html><head><script>start()</script></head></html>"
    )
    (static / "_app" / "immutable" / "a.js").write_text("x")
    monkeypatch.setenv("STATIC_DIR", str(static))
    with TestClient(create_app(Settings())) as c:
        for path in ("/", "/trip/japan", "/search/ski", "/status", "/more"):
            r = c.get(path)
            assert r.status_code == 200 and "start()" in r.text
            assert r.headers["cache-control"] == "no-cache"
        assert (
            inline_script_hashes(static / "index.html")[0] in r.headers["content-security-policy"]
        )
        assert "immutable" in c.get("/_app/immutable/a.js").headers["cache-control"]
        assert c.get("/api/unknown").status_code == 404
        assert c.get("/robots.txt").text.startswith("User-agent")
        (tmp_path / "secret.txt").write_text("not for you")
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/_app/..%2f..%2fsecret.txt"):
            assert "not for you" not in c.get(path).text  # never outside the build


# ---------------------------------------------------------------------------
# public reads
# ---------------------------------------------------------------------------


def test_overview_lists_the_demo_trip_and_each_search(client: TestClient) -> None:
    conn = db(client.app.state.settings)  # type: ignore[attr-defined]
    conn.execute(
        "INSERT INTO notifications (kind, title, body, url, created_at) VALUES "
        "('price', '✈ $612 Tokyo', 'lowest yet', '/trip/tokyo-spring', ?)",
        (iso(NOW),),
    )
    conn.commit()
    conn.close()
    r = client.get("/api/overview")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    body = r.json()
    assert body["generated_at"] == "2027-01-10T12:00:00Z"
    (trip,) = body["trips"]
    assert trip["slug"] == TRIP and trip["label"] == "Tokyo" and trip["destination"] == "HND"
    assert trip["price"] == trip["series"][-1]["price"] and trip["price_at"].startswith(
        "2027-01-10"
    )
    assert trip["verdict"] in ("BOOK", "LEAN BOOK", "HOLD", "WAIT")
    assert trip["target_price"] == 80000
    assert trip["all_time_low"] <= trip["price"] and trip["observations"] == 46
    assert len(trip["series"]) == 30 and trip["link"].startswith("https://www.google.com/travel")
    assert trip["days_to_departure"] >= 150
    assert {s["slug"] for s in body["searches"]} == {"ski-weekends", "tokyo-week"}
    week = next(s for s in body["searches"] if s["slug"] == SEARCH)
    assert week["pairs"] > 0 and week["priced"] == week["pairs"]
    assert week["best"]["price"] == min(
        p["price"]
        for p in client.get(f"/api/searches/{SEARCH}").json()["grid"][0]["pairs"]
        + client.get(f"/api/searches/{SEARCH}").json()["grid"][1]["pairs"]
    )
    assert body["notifications"][0]["title"] == "✈ $612 Tokyo"
    assert body["deals"] and body["deals"][0]["watch"]
    assert body["health"]["ok"] is True


def test_trip_page(client: TestClient, settings: Settings) -> None:
    conn = db(settings)
    qid = conn.execute(
        "SELECT q.id FROM queries q JOIN routes r ON r.id = q.route_id WHERE q.pattern = ? "
        "AND r.destination = 'HND'",
        (TRIP,),
    ).fetchone()[0]
    raw = {
        "segments": [
            {"from": "SEA", "to": "HND", "dep": "2027-05-15 13:05", "arr": "2027-05-16 16:20"},
        ],
        "layovers": [],
    }
    connecting = {
        "segments": [
            {"from": "SEA", "to": "SFO", "dep": "2027-05-15 06:00", "arr": "2027-05-15 08:10"},
            {"from": "SFO", "to": "HND", "dep": "2027-05-15 11:00", "arr": "None None"},
        ],
        "layovers": [{"min": 170, "at": "SFO"}],
    }
    for i, (price, r) in enumerate(((99000, raw), (91000, connecting))):
        conn.execute(
            "INSERT INTO observations (query_id, observed_at, price_cents, airline, stops, "
            "duration_min, is_best, raw, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'google_html')",
            (qid, iso(NOW), price, "ANA", i, 600 + i, 1 if i else 0, json.dumps(r)),
        )
    conn.execute(
        "INSERT INTO alerts_sent (query_id, observation_id, sent_at, reason, price_cents) "
        "VALUES (?, 1, ?, 'threshold', 79000)",
        (qid, iso(NOW - timedelta(days=3))),
    )
    conn.commit()
    conn.close()
    body = client.get(f"/api/trips/{TRIP}").json()
    assert body["query_id"] == qid and body["price"] == 91000 and body["price_at"] == iso(NOW)
    assert len(body["series"]) == 47  # the whole series
    cheap, other = body["offers"]
    assert cheap["price"] == 91000 and cheap["stops"] == 1
    assert cheap["layovers"] == [{"minutes": 170, "airport": "SFO"}]
    assert cheap["departs"] == "2027-05-15 06:00" and cheap["arrives"] is None
    assert other["departs"] == "2027-05-15 13:05" and other["arrives"] == "2027-05-16 16:20"
    assert body["offers_at"] == iso(NOW)
    assert body["advice"]["verdict"] == body["verdict"] and body["advice"]["reasons"]
    # The demo records its own alert at the trip's lowest price; ours is the newest.
    assert body["alerts"][0] == {
        "sent_at": iso(NOW - timedelta(days=3)),
        "reason": "threshold",
        "price": 79000,
    }
    assert [a["reason"] for a in body["alerts"]] == ["threshold", "all_time_low"]
    assert client.get("/api/trips/nowhere").status_code == 404


def test_paused_trip_still_shows_its_history(owner: TestClient, settings: Settings) -> None:
    trip = watches.get_trip(db(settings), TRIP)
    r = owner.put(
        f"/api/admin/trips/{trip.id}/status", json={"status": "paused"}, headers=SAME_SITE
    )
    assert r.status_code == 200 and r.json()["job_id"] is None
    body = owner.get(f"/api/trips/{TRIP}").json()
    assert body["status"] == "paused" and body["verdict"] is None
    assert body["observations"] == 46 and body["price"] is not None


def test_search_grid(client: TestClient) -> None:
    body = client.get(f"/api/searches/{SEARCH}").json()
    assert body["destinations"] == ["HND", "NRT"] and body["nights"] == 7
    assert [d["code"] for d in body["grid"]] == ["HND", "NRT"]
    assert body["grid"][0]["label"] == "Tokyo"
    for dest in body["grid"]:
        pairs = dest["pairs"]
        assert pairs and all(p["destination"] == dest["code"] for p in pairs)
        cheapest = [p for p in pairs if "cheapest" in p["flags"]]
        assert len(cheapest) == 1
        assert cheapest[0]["price"] == min(p["price"] for p in pairs)
        p = pairs[0]
        assert p["source"] == "detail" and p["age_hours"] is not None and p["link"]
        assert p["origin"] == "SEA"
    assert client.get("/api/searches/nowhere").status_code == 404


def test_calendar_price_is_marked_as_an_estimate(client: TestClient, settings: Settings) -> None:
    conn = db(settings)
    cfg = config_mod.load(strict_secrets=False, settings=settings)
    watches.create_search(
        conn,
        cfg,
        {
            "name": "Reno",
            "origin": "SEA",
            "destinations": ["RNO"],
            "depart_dows": ["FRI"],
            "nights": 2,
            "target_price": 30000,
        },
        today=TODAY,
    )
    qid = conn.execute(
        "SELECT q.id FROM queries q WHERE q.pattern = 'reno' ORDER BY depart_date LIMIT 1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO observations (query_id, observed_at, price_cents, is_best, source) "
        "VALUES (?, ?, 21000, 0, 'google_calendar')",
        (qid, iso(NOW - timedelta(hours=5))),
    )
    conn.commit()
    conn.close()
    body = client.get("/api/searches/reno").json()
    assert body["priced"] == 1 and body["pairs"] > 1
    best = body["best"]
    assert best["query_id"] == qid and best["source"] == "calendar" and best["price"] == 21000
    assert best["age_hours"] == 5.0
    assert set(best["flags"]) == {"cheapest", "under_target", "estimate"}
    hist = client.get(f"/api/queries/{qid}/history").json()
    assert hist["points"] == [] and hist["calendar"] == [
        {"at": iso(NOW - timedelta(hours=5)), "price": 21000}
    ]


def test_query_history(client: TestClient, settings: Settings) -> None:
    conn = db(settings)
    qid = conn.execute("SELECT id FROM queries WHERE pattern = ?", (TRIP,)).fetchone()[0]
    conn.close()
    body = client.get(f"/api/queries/{qid}/history").json()
    assert body["origin"] == "SEA" and body["destination"] == "HND" and body["active"] is True
    assert len(body["points"]) == 46 and body["target_price"] == 80000
    ats = [p["at"] for p in body["points"]]
    assert ats == sorted(ats)
    assert client.get("/api/queries/99999/history").status_code == 404


def test_deals(client: TestClient) -> None:
    body = client.get("/api/deals").json()
    assert body and all(d["watch"] for d in body)
    assert body[0]["price"] and body[0]["title"]
    assert len(client.get("/api/deals?limit=1").json()) == 1
    assert client.get("/api/deals?limit=0").status_code == 422


def test_status(client: TestClient, settings: Settings) -> None:
    settings.backup_dir.mkdir(parents=True)
    (settings.backup_dir / "flights-20270109.db").write_bytes(b"x" * 10)
    body = client.get("/api/status").json()
    assert body["ok"] is True and body["problems"] == []
    assert body["budget"]["limit"] == 50
    used = body["budget"]["used"]
    assert used > 0 and body["budget"]["remaining"] == max(0, 50 - used)
    assert body["requests_by_day"] and all(d["outcomes"] for d in body["requests_by_day"])
    assert body["recent_runs"] and body["recent_runs"][0]["kind"]
    assert body["backups"] == [
        {"name": "flights-20270109.db", "bytes": 10, "created_at": body["backups"][0]["created_at"]}
    ]
    assert body["database_bytes"] > 0 and body["observations"] > 0 and body["active_pairs"] > 0
    assert body["newest_price_at"] == f"{TODAY.isoformat()}T10:00:00Z"
    assert body["cooldown_until"] is None and body["worker_heartbeat_age_s"] is None


def test_airport_search(client: TestClient) -> None:
    body = client.get("/api/airports?q=sea").json()
    assert body[0]["iata"] == "SEA" and len(body) <= 10
    assert client.get("/api/airports?q=").status_code == 422


# ---------------------------------------------------------------------------
# rate limits
# ---------------------------------------------------------------------------


def test_public_reads_are_limited_to_60_a_minute(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ratelimit, "now", lambda: 1000.0)
    for _ in range(60):
        assert client.get("/api/deals").status_code == 200
    r = client.get("/api/deals")
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert client.get("/api/livez").status_code == 200  # the container health check is free
    monkeypatch.setattr(ratelimit, "now", lambda: 1002.0)  # two seconds refill two
    assert client.get("/api/deals").status_code == 200


def test_reads_per_minute_is_a_setting(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit, "now", lambda: 1000.0)
    seed(settings)
    with TestClient(create_app(settings.model_copy(update={"reads_per_minute": 5}))) as c:
        for _ in range(5):
            assert c.get("/api/deals").status_code == 200
        assert c.get("/api/deals").status_code == 429


def test_airport_search_is_limited_to_30_a_minute(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ratelimit, "now", lambda: 1000.0)
    for _ in range(30):
        assert client.get("/api/airports?q=lax").status_code == 200
    assert client.get("/api/airports?q=lax").status_code == 429


def test_proxy_header_counts_only_behind_a_trusted_proxy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ratelimit, "now", lambda: 1000.0)
    for i in range(31):  # a new forged address each time does not escape the limit
        r = client.get("/api/airports?q=lax", headers={"CF-Connecting-IP": f"10.0.0.{i}"})
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# sign-in
# ---------------------------------------------------------------------------


def test_sign_in_and_out(client: TestClient) -> None:
    assert client.get("/api/auth/me").json() == {"is_owner": False, "owner_login_enabled": True}
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers=SAME_SITE)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "fw_owner=" in cookie and "HttpOnly" in cookie and "Path=/api" in cookie
    assert "samesite=strict" in cookie.lower()
    assert client.get("/api/auth/me").json()["is_owner"] is True
    assert client.get("/api/admin/trips").status_code == 200
    assert client.post("/api/auth/logout", headers=SAME_SITE).json()["is_owner"] is False
    assert client.get("/api/admin/trips").status_code == 401


def test_wrong_password(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"password": "nope"}, headers=SAME_SITE)
    assert r.status_code == 401 and r.json()["detail"] == "wrong password"


def test_five_wrong_passwords_lock_out(client: TestClient) -> None:
    for _ in range(5):
        r = client.post("/api/auth/login", json={"password": "guess"}, headers=SAME_SITE)
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers=SAME_SITE)
    assert r.status_code == 429 and "too many wrong passwords" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) > 0


def test_login_needs_this_sites_origin(client: TestClient) -> None:
    r = client.post(
        "/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 403


def test_sign_in_is_off_without_a_long_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OWNER_PASSWORD", "short")
    with TestClient(create_app(Settings())) as c:
        assert c.get("/api/auth/me").json() == {"is_owner": False, "owner_login_enabled": False}
        r = c.post("/api/auth/login", json={"password": "short"}, headers=SAME_SITE)
        assert r.status_code == 404
        assert c.get("/api/meta").json()["owner_login_enabled"] is False


def test_logout_all_ends_every_session(owner: TestClient, settings: Settings, capsys) -> None:
    assert main(["owner", "logout-all"]) == 0
    assert "ended 1 owner session" in capsys.readouterr().out
    assert owner.get("/api/admin/trips").status_code == 401


def test_first_sign_in_creates_the_database(settings: Settings) -> None:
    with TestClient(create_app(settings)) as c:
        sign_in(c)
        assert c.get("/api/admin/trips").json() == []
    assert settings.database.exists()


# ---------------------------------------------------------------------------
# admin
# ---------------------------------------------------------------------------

ADMIN_READS = [
    "/api/admin/trips",
    "/api/admin/searches",
    "/api/admin/deal-watches",
    "/api/admin/deal-feeds",
    "/api/admin/settings",
    "/api/admin/jobs/1",
]


@pytest.mark.parametrize("path", ADMIN_READS)
def test_admin_without_a_session_is_401(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_admin_write_without_a_session_is_401(client: TestClient) -> None:
    r = client.post("/api/admin/trips", json={}, headers=SAME_SITE)
    assert r.status_code == 401
    r = client.post("/api/admin/jobs", json={"kind": "poll_deals"}, headers=SAME_SITE)
    assert r.status_code == 401


def test_admin_write_with_a_foreign_origin_is_403(owner: TestClient) -> None:
    evil = {"Origin": "https://evil.example"}
    assert (
        owner.post("/api/admin/jobs", json={"kind": "poll_deals"}, headers=evil).status_code == 403
    )
    assert owner.post("/api/admin/jobs", json={"kind": "poll_deals"}).status_code == 403  # none
    assert owner.delete("/api/admin/trips/1", headers=evil).status_code == 403
    assert owner.get("/api/admin/trips").status_code == 200  # reads need no Origin


def test_public_url_is_an_allowed_origin(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    monkeypatch.setenv("PUBLIC_URL", "https://flights.example.com")
    with TestClient(create_app(Settings())) as c:
        r = c.post(
            "/api/auth/login",
            json={"password": PASSWORD},
            headers={"Origin": "https://flights.example.com"},
        )
        assert r.status_code == 200


TRIP_IN = {
    "name": "Ski trip",
    "origin": "SEA",
    "destination": "SLC",
    "depart_date": "2027-02-11",
    "return_date": "2027-02-15",
    "target_price": 15000,
}


def test_create_update_status_and_delete_a_trip(owner: TestClient, settings: Settings) -> None:
    r = owner.post("/api/admin/trips", json=TRIP_IN, headers=SAME_SITE)
    assert r.status_code == 201, r.text
    saved = r.json()
    trip = saved["trip"]
    assert trip["slug"] == "ski-trip" and trip["status"] == "active"
    job = owner.get(f"/api/admin/jobs/{saved['job_id']}").json()
    assert job["kind"] == "fetch_trip" and job["payload"] == {"trip": "ski-trip"}
    assert job["status"] == "queued" and job["requested_by"] == "owner"
    # the date pair exists the moment the trip is saved
    assert owner.get("/api/trips/ski-trip").json()["query_id"] is not None

    r = owner.put(
        f"/api/admin/trips/{trip['id']}",
        json={**TRIP_IN, "target_price": 12000, "notes": "go"},
        headers=SAME_SITE,
    )
    assert r.status_code == 200 and r.json()["trip"]["target_price"] == 12000
    assert r.json()["job_id"] is not None

    r = owner.put(
        f"/api/admin/trips/{trip['id']}/status", json={"status": "booked"}, headers=SAME_SITE
    )
    assert r.json()["trip"]["status"] == "booked" and r.json()["job_id"] is None
    assert {t["slug"] for t in owner.get("/api/admin/trips").json()} == {TRIP, "ski-trip"}

    assert owner.delete(f"/api/admin/trips/{trip['id']}", headers=SAME_SITE).json() == {"ok": True}
    assert owner.get("/api/trips/ski-trip").status_code == 404
    assert owner.delete(f"/api/admin/trips/{trip['id']}", headers=SAME_SITE).status_code == 404


def test_trip_validation_errors_are_readable(owner: TestClient) -> None:
    r = owner.post("/api/admin/trips", json={**TRIP_IN, "destination": "XYZ"}, headers=SAME_SITE)
    assert r.status_code == 422
    assert r.json() == {"detail": "destination: XYZ is not an airport with scheduled flights"}
    r = owner.post(
        "/api/admin/trips", json={**TRIP_IN, "return_date": "2027-02-01"}, headers=SAME_SITE
    )
    assert r.json()["detail"] == "the return date must be after the departure date"
    r = owner.post("/api/admin/trips", json={"origin": "SEA"}, headers=SAME_SITE)
    assert r.json()["detail"] == "name: required; destination: required; depart_date: required"
    r = owner.post("/api/admin/trips", json={**TRIP_IN, "slug": TRIP}, headers=SAME_SITE)
    assert r.status_code == 422 and "already used" in r.json()["detail"]


def test_a_trip_over_the_limit_is_refused_with_the_count(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watches, "MAX_ACTIVE_TRIPS", 1)
    r = owner.post("/api/admin/trips", json=TRIP_IN, headers=SAME_SITE)
    assert r.status_code == 422
    assert r.json()["detail"].startswith("That would make 2 active trips; the limit is 1.")
    assert {t["slug"] for t in owner.get("/api/admin/trips").json()} == {TRIP}


SEARCH_IN = {
    "name": "Reno weekends",
    "origin": "SEA",
    "destinations": ["RNO"],
    "depart_dows": ["FRI"],
    "nights": 2,
}


def test_searches(owner: TestClient) -> None:
    r = owner.post("/api/admin/searches", json=SEARCH_IN, headers=SAME_SITE)
    assert r.status_code == 201, r.text
    s = r.json()["search"]
    job = owner.get(f"/api/admin/jobs/{r.json()['job_id']}").json()
    assert job["kind"] == "fetch_search" and job["payload"] == {"search": "reno-weekends"}
    assert owner.get("/api/searches/reno-weekends").json()["pairs"] > 0

    r = owner.put(
        f"/api/admin/searches/{s['id']}", json={**SEARCH_IN, "nights": 3}, headers=SAME_SITE
    )
    assert r.json()["search"]["nights"] == 3
    r = owner.put(
        f"/api/admin/searches/{s['id']}/status", json={"status": "booked"}, headers=SAME_SITE
    )
    assert r.status_code == 422 and "status" in r.json()["detail"]
    r = owner.put(
        f"/api/admin/searches/{s['id']}/status", json={"status": "paused"}, headers=SAME_SITE
    )
    assert r.json()["search"]["status"] == "paused" and r.json()["job_id"] is None
    assert owner.get("/api/searches/reno-weekends").json()["pairs"] == 0
    r = owner.post(
        "/api/admin/searches", json={**SEARCH_IN, "depart_dows": ["XYZ"]}, headers=SAME_SITE
    )
    assert r.status_code == 422 and "is not a weekday" in r.json()["detail"]
    assert len(owner.get("/api/admin/searches").json()) == 3
    assert owner.delete(f"/api/admin/searches/{s['id']}", headers=SAME_SITE).json()["ok"]
    assert len(owner.get("/api/admin/searches").json()) == 2


def test_deal_watches_and_the_headline_test(owner: TestClient) -> None:
    body = {
        "name": "Hawaii",
        "origins": ["Seattle"],
        "destinations": ["hawaii"],
        "max_price": 40000,
    }
    r = owner.post("/api/admin/deal-watches", json=body, headers=SAME_SITE)
    assert r.status_code == 201
    w = r.json()
    r = owner.post(
        "/api/admin/deal-watches/test",
        json={"headline": "Alaska Air: Seattle – Honolulu, Hawaii. $298 roundtrip"},
        headers=SAME_SITE,
    )
    assert r.json() == {"price": 29800, "matched": "Hawaii", "matches": ["Hawaii"]}
    r = owner.post(
        "/api/admin/deal-watches/test",
        json={"headline": "Seattle – Honolulu, Hawaii. $598 roundtrip"},
        headers=SAME_SITE,
    )
    assert r.json()["matched"] is None  # over the watch's price
    r = owner.put(
        f"/api/admin/deal-watches/{w['id']}", json={**body, "max_price": 70000}, headers=SAME_SITE
    )
    assert r.json()["max_price"] == 70000
    r = owner.put(
        f"/api/admin/deal-watches/{w['id']}/status", json={"status": "paused"}, headers=SAME_SITE
    )
    assert r.json()["status"] == "paused"
    assert owner.delete(f"/api/admin/deal-watches/{w['id']}", headers=SAME_SITE).json()["ok"]
    assert (
        owner.put(f"/api/admin/deal-watches/{w['id']}", json=body, headers=SAME_SITE).status_code
        == 404
    )


FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Deals</title>
<item><title>Alaska: Seattle - Honolulu $298</title><guid>a1</guid></item>
</channel></rss>"""


def test_deal_feeds(owner: TestClient) -> None:
    url = "https://www.theflightdeal.com/feed/"
    t = FakeTransport([(200, None, FEED_XML)])
    owner.app.state.feed_transport = t  # type: ignore[attr-defined]
    r = owner.post("/api/admin/deal-feeds", json={"url": url, "name": "TFD"}, headers=SAME_SITE)
    assert r.status_code == 201
    assert [c["url"] for c in t.calls] == [url]  # fetched once before saving
    f = r.json()
    r = owner.post("/api/admin/deal-feeds", json={"url": url}, headers=SAME_SITE)
    assert r.status_code == 422 and "already a feed" in r.json()["detail"]
    assert len(t.calls) == 1  # a duplicate is refused without fetching
    r = owner.post("/api/admin/deal-feeds", json={"url": "not a url!!"}, headers=SAME_SITE)
    assert r.status_code == 422 and r.json()["detail"].startswith("url:")
    r = owner.put(
        f"/api/admin/deal-feeds/{f['id']}/status", json={"status": "paused"}, headers=SAME_SITE
    )
    assert r.json()["status"] == "paused"
    r = owner.put(
        f"/api/admin/deal-feeds/{f['id']}", json={"url": url, "name": "x"}, headers=SAME_SITE
    )
    assert r.json()["name"] == "x"
    assert len(t.calls) == 1  # same address: not fetched again
    assert [x["url"] for x in owner.get("/api/admin/deal-feeds").json()] == [url]
    assert owner.delete(f"/api/admin/deal-feeds/{f['id']}", headers=SAME_SITE).json()["ok"]


@pytest.mark.parametrize(
    ("reply", "says"),
    [
        ((200, None, "<!DOCTYPE html><html><body>Home</body></html>"), "a web page, not a feed"),
        ((404, None, "gone"), "HTTP 404"),
        ((200, None, '{"items": []}'), "not an RSS or Atom feed"),
        ((200, None, "<svg xmlns='http://www.w3.org/2000/svg'/>"), "not an RSS or Atom feed"),
        (TransportError("connection refused"), "couldn't fetch it"),
    ],
)
def test_a_url_that_is_not_a_feed_is_refused(
    owner: TestClient, settings: Settings, reply: object, says: str
) -> None:
    owner.app.state.feed_transport = FakeTransport([reply])  # type: ignore[attr-defined]
    url = "https://example.com/deals"
    r = owner.post("/api/admin/deal-feeds", json={"url": url}, headers=SAME_SITE)
    assert r.status_code == 422
    assert r.json()["detail"].startswith(f"url: {url} can't be added: ")
    assert says in r.json()["detail"]
    assert owner.get("/api/admin/deal-feeds").json() == []  # nothing saved


def test_changing_a_feed_address_checks_the_new_one(owner: TestClient) -> None:
    t = FakeTransport([(200, None, FEED_XML), (200, None, "<html>moved</html>")])
    owner.app.state.feed_transport = t  # type: ignore[attr-defined]
    f = owner.post(
        "/api/admin/deal-feeds", json={"url": "https://a.example.com/feed"}, headers=SAME_SITE
    ).json()
    r = owner.put(
        f"/api/admin/deal-feeds/{f['id']}",
        json={"url": "https://b.example.com/feed"},
        headers=SAME_SITE,
    )
    assert r.status_code == 422 and "a web page" in r.json()["detail"]
    assert owner.get("/api/admin/deal-feeds").json()[0]["url"] == "https://a.example.com/feed"


def test_the_fake_source_adds_feeds_without_fetching(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLIGHTTRACK_SOURCE", "fake")
    set_clock(lambda: NOW)
    seed(settings)
    with TestClient(create_app(Settings())) as c:
        t = FakeTransport([])
        c.app.state.feed_transport = t  # type: ignore[attr-defined]
        sign_in(c)
        r = c.post(
            "/api/admin/deal-feeds", json={"url": "https://a.example.com/feed"}, headers=SAME_SITE
        )
        assert r.status_code == 201 and t.calls == []


def test_search_preview_counts_without_saving(owner: TestClient, settings: Settings) -> None:
    before = owner.get("/api/admin/collecting").json()["active_pairs"]
    r = owner.post("/api/admin/searches/preview", json=SEARCH_IN, headers=SAME_SITE)
    assert r.status_code == 200, r.text
    p = r.json()
    # Fridays from 14 days out to the end of the horizon, one destination.
    assert p["pairs"] > 10
    assert p["total_pairs"] == before + p["pairs"]
    assert p["max_pairs"] == watches.MAX_ACTIVE_PAIRS
    cfg = owner.app.state.cfg  # type: ignore[attr-defined]
    assert p["detail_per_day"] == fetch.detail_fetches_per_day(cfg)
    assert p["refresh_days"] == -(-p["total_pairs"] // p["detail_per_day"])
    assert len(owner.get("/api/admin/searches").json()) == 2  # nothing saved
    assert owner.get("/api/admin/collecting").json()["active_pairs"] == before

    # Saving it gives the same count.
    saved = owner.post("/api/admin/searches", json=SEARCH_IN, headers=SAME_SITE).json()["search"]
    assert owner.get("/api/admin/collecting").json()["active_pairs"] == p["total_pairs"]

    # Previewing a change to it: two weekdays instead of one, counted once.
    r = owner.post(
        f"/api/admin/searches/preview?search_id={saved['id']}",
        json={**SEARCH_IN, "depart_dows": ["FRI", "SAT"]},
        headers=SAME_SITE,
    )
    q = r.json()
    assert q["pairs"] > p["pairs"] and q["total_pairs"] == p["total_pairs"] + (
        q["pairs"] - p["pairs"]
    )
    assert owner.get("/api/admin/searches").json()[-1]["depart_dows"] == ["FRI"]

    r = owner.post(
        "/api/admin/searches/preview", json={**SEARCH_IN, "nights": -1}, headers=SAME_SITE
    )
    assert r.status_code == 422 and r.json()["detail"].startswith("nights:")
    r = owner.post("/api/admin/searches/preview?search_id=999", json=SEARCH_IN, headers=SAME_SITE)
    assert r.status_code == 404


def test_detail_fetches_per_day_leaves_room_for_the_sweep_and_the_canary() -> None:
    cfg = config_mod.load(strict_secrets=False, settings=Settings())
    off = dataclasses.replace(cfg, calendar=dataclasses.replace(cfg.calendar, enabled=False))
    on = dataclasses.replace(
        cfg, calendar=dataclasses.replace(cfg.calendar, enabled=True, max_calls_per_run=12)
    )
    budget = cfg.collect.daily_request_budget
    assert fetch.detail_fetches_per_day(off) == budget - 1
    assert fetch.detail_fetches_per_day(on) == budget - 12 - 1
    tiny = dataclasses.replace(on, collect=dataclasses.replace(cfg.collect, daily_request_budget=5))
    assert fetch.detail_fetches_per_day(tiny) == 1


def test_collecting_reports_limits_budget_and_cooldown(
    owner: TestClient, settings: Settings
) -> None:
    c = owner.get("/api/admin/collecting").json()
    assert c["cooldown_until"] is None
    assert c["budget"]["limit"] == 50
    assert c["active_trips"] == 1 and c["max_trips"] == watches.MAX_ACTIVE_TRIPS
    assert c["active_searches"] == 2 and c["max_searches"] == watches.MAX_ACTIVE_SEARCHES
    assert 0 < c["active_pairs"] <= c["max_pairs"]
    assert c["default_feeds"] == list(owner.app.state.cfg.deals.feeds)  # type: ignore[attr-defined]
    conn = db(settings)
    set_state(conn, fetch.STATE_COOLDOWN_UNTIL, "2027-01-10T15:00:00Z")
    assert owner.get("/api/admin/collecting").json()["cooldown_until"] == "2027-01-10T15:00:00Z"


def test_check_now_is_refused_during_a_cooldown(owner: TestClient, settings: Settings) -> None:
    conn = db(settings)
    set_state(conn, fetch.STATE_COOLDOWN_UNTIL, "2027-01-10T15:00:00Z")
    for body in ({"kind": "fetch_trip", "trip": TRIP}, {"kind": "fetch_search", "search": SEARCH}):
        r = owner.post("/api/admin/jobs", json=body, headers=SAME_SITE)
        assert r.status_code == 409
        assert r.json()["detail"].startswith("Collecting is paused until 2027-01-10T15:00:00Z")
    assert jobs.pending(conn) == 0
    # Polling the deal feeds is not a Google request: it is still queued.
    r = owner.post("/api/admin/jobs", json={"kind": "poll_deals"}, headers=SAME_SITE)
    assert r.status_code == 201
    set_clock(lambda: datetime(2027, 1, 10, 15, 1, tzinfo=UTC))  # the cooldown is over
    r = owner.post("/api/admin/jobs", json={"kind": "fetch_trip", "trip": TRIP}, headers=SAME_SITE)
    assert r.status_code == 201


def test_alert_settings_are_stored_and_used(owner: TestClient, settings: Settings) -> None:
    assert owner.get("/api/admin/settings").json() == {
        "dedup_days": 7,
        "dedup_further_drop_pct": 10.0,
        "percentile_alerts": False,
    }
    new = {"dedup_days": 3, "dedup_further_drop_pct": 15.0, "percentile_alerts": True}
    assert owner.put("/api/admin/settings", json=new, headers=SAME_SITE).json() == new
    assert owner.get("/api/admin/settings").json() == new
    r = owner.put("/api/admin/settings", json={**new, "dedup_days": 0}, headers=SAME_SITE)
    assert r.status_code == 422 and r.json()["detail"].startswith("dedup_days:")
    cfg = config_mod.load(strict_secrets=False, settings=settings)
    conn = db(settings)
    applied = app_settings.apply(conn, cfg).alerts
    assert (applied.dedup_days, applied.dedup_further_drop_pct) == (3, 15.0)
    assert applied.percentile_phase2_enabled is True
    assert applied.max_per_run == cfg.alerts.max_per_run  # the rest is the file's


def test_alert_run_reads_the_stored_settings(
    owner: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    monkeypatch.setattr(
        alert, "find_candidates", lambda conn, cfg: (seen.append(cfg.alerts.dedup_days), ([], 0))[1]
    )
    owner.put(
        "/api/admin/settings",
        json={"dedup_days": 2, "dedup_further_drop_pct": 10, "percentile_alerts": False},
        headers=SAME_SITE,
    )
    alert.run(db(settings), config_mod.load(strict_secrets=False, settings=settings), verbose=False)
    assert seen == [2]


def test_jobs(owner: TestClient, settings: Settings) -> None:
    r = owner.post("/api/admin/jobs", json={"kind": "fetch_trip", "trip": TRIP}, headers=SAME_SITE)
    assert r.status_code == 201
    job = r.json()
    assert job["status"] == "queued" and job["payload"] == {"trip": TRIP}
    conn = db(settings)
    claimed = jobs.claim_next(conn)
    assert claimed and claimed["id"] == job["id"]
    jobs.finish(conn, job["id"], error="in cooldown until 2027-01-10T13:00:00Z")
    got = owner.get(f"/api/admin/jobs/{job['id']}").json()
    assert got["status"] == "failed" and got["error"].startswith("in cooldown")
    r = owner.post("/api/admin/jobs", json={"kind": "fetch_trip", "trip": "x"}, headers=SAME_SITE)
    assert r.status_code == 404
    r = owner.post("/api/admin/jobs", json={"kind": "fetch_search"}, headers=SAME_SITE)
    assert r.status_code == 422
    r = owner.post("/api/admin/jobs", json={"kind": "reboot"}, headers=SAME_SITE)
    assert r.status_code == 422 and r.json()["detail"].startswith("kind:")
    r = owner.post("/api/admin/jobs", json={"kind": "poll_deals"}, headers=SAME_SITE)
    assert r.json()["kind"] == "poll_deals"
    assert owner.get("/api/admin/jobs/999").status_code == 404


# ---------------------------------------------------------------------------
# the schema the frontend is generated from
# ---------------------------------------------------------------------------

SCHEMA = REPO_ROOT / "frontend" / "src" / "lib" / "api-schema.json"


def test_committed_schema_is_current() -> None:
    assert SCHEMA.exists(), (
        "run `uv run flighttrack schema --out ../frontend/src/lib/api-schema.json`"
    )
    assert SCHEMA.read_text(encoding="utf-8") == api_schema(), (
        "the API changed: run `uv run flighttrack schema --out ../frontend/src/lib/api-schema.json`"
    )


def test_schema_command(tmp_path, capsys) -> None:
    out = tmp_path / "s.json"
    assert main(["schema", "--out", str(out)]) == 0
    schema = json.loads(out.read_text())
    assert {"OverviewOut", "TripOut", "SearchOut", "StatusOut", "HealthOut"} <= set(schema["$defs"])
    props = schema["$defs"]["TripSummary"]["properties"]
    assert all("title" not in p for p in props.values())
    assert main(["schema"]) == 0 and json.loads(capsys.readouterr().out) == schema
