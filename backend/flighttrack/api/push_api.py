"""Push alert subscriptions (M8). Only the owner subscribes: the site is public and
read-only, and the alerts are the owner's. Every call needs the owner session and this
site's Origin; subscriptions must point at a real browser push service
(`push.is_push_service`), and the server keeps at most `push.MAX_SUBSCRIPTIONS`.
Copied from Snow Watch's `api/push_api.py`, with its per-visitor openness closed."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import push
from ..push import AlertPrefs, Endpoint, PushOut, SubscribeIn
from ..settings import Settings
from .admin import RW
from .auth import Owner
from .ratelimit import RateLimiter


def make_push_router() -> APIRouter:
    router = APIRouter(prefix="/api/push", tags=["push"])
    # Saving choices: a burst of 10, then one a minute. Tests: 3, then one per 10 min.
    saves = RateLimiter(rate=1 / 60, burst=10)
    tests = RateLimiter(rate=1 / 600, burst=3)

    def settings_of(request: Request) -> Settings:
        settings: Settings = request.app.state.settings
        if not push.enabled(settings):
            raise HTTPException(404, "push alerts are not set up on this server")
        return settings

    @router.get("/key")
    def key(request: Request, _: Owner) -> dict[str, str]:
        """The VAPID public key the browser subscribes with."""
        return {"key": settings_of(request).vapid_public_key}

    @router.post("/subscribe", response_model=PushOut)
    def subscribe(body: SubscribeIn, request: Request, _: Owner, conn: RW) -> PushOut:
        """Subscribe this device, or save its choice of kinds (price, deal, health)."""
        settings_of(request)
        saves.check(request)
        try:
            push.subscribe(conn, body)
        except push.SubscriptionLimitError as e:
            raise HTTPException(
                422, f"At most {push.MAX_SUBSCRIPTIONS} devices can get alerts. Turn one off first."
            ) from e
        return PushOut(ok=True)

    @router.post("/prefs", response_model=AlertPrefs)
    def prefs(body: Endpoint, request: Request, _: Owner, conn: RW) -> AlertPrefs:
        """The saved choices of this device's subscription."""
        settings_of(request)
        saves.check(request, cost=0.2)
        found = push.prefs(conn, body.endpoint)
        if found is None:
            raise HTTPException(404, "this device isn't subscribed")
        return found

    @router.post("/unsubscribe", response_model=PushOut)
    def unsubscribe(body: Endpoint, request: Request, _: Owner, conn: RW) -> PushOut:
        saves.check(request)
        return PushOut(ok=push.unsubscribe(conn, body.endpoint))

    @router.post("/test", response_model=PushOut)
    def test(body: Endpoint, request: Request, _: Owner, conn: RW) -> PushOut:
        """Send this device a test alert."""
        settings = settings_of(request)
        tests.check(request)
        sub = next((s for s in push.subscriptions(conn) if s.endpoint == body.endpoint), None)
        if sub is None:
            raise HTTPException(404, "this device isn't subscribed")
        sender = getattr(request.app.state, "push_sender", push.webpush_sender)
        ok = push.send(
            conn,
            settings,
            sub,
            {
                "title": "Flight Watch alerts are on",
                "body": "Price drops, found deals and collector trouble will show up here.",
                "url": "/more",
                "tag": "test",
            },
            sender,
        )
        return PushOut(ok=ok, sent=int(ok), detail=None if ok else "the push service refused it")

    return router
