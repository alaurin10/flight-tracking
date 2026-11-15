"""The owner role: one password, set on the server, unlocks editing.

Flight Watch has no accounts (copied from Snow Watch). Everyone can read prices;
whoever knows OWNER_PASSWORD can add, change and remove what is tracked.
Signing in stores a random token in an HttpOnly cookie and its SHA-256 in
`owner_sessions`, so a session can be ended (sign out, or `flighttrack owner
logout-all` after changing the password) and survives restarts. With
OWNER_PASSWORD empty (or shorter than 12 characters), sign-in is off and what is
tracked is managed with `flighttrack import-config`.

State-changing owner requests must come from this site's own pages (the Origin
header), on top of the SameSite=Strict cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..settings import Settings
from ..store.db import connect, open_db
from .ratelimit import LoginThrottle, client_key

log = logging.getLogger("flighttrack.auth")
COOKIE = "fw_owner"
SESSION_DAYS = 30
MIN_PASSWORD = 12
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Viewer:
    is_owner: bool


def owner_login_enabled(settings: Settings) -> bool:
    """A password is set and long enough. A short one is refused (and logged at start)."""
    return len(settings.owner_password) >= MIN_PASSWORD


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _session_valid(settings: Settings, token: str) -> bool:
    if not settings.database.exists():
        return False
    try:
        conn = connect(settings.database, readonly=True)
        try:
            row = conn.execute(
                "SELECT expires_at FROM owner_sessions WHERE token_hash = ?", (_hash(token),)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return row is not None and datetime.fromisoformat(row["expires_at"]) > _now()


def check_origin(request: Request) -> None:
    """Refuse state-changing requests that don't come from this site's own pages."""
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(403, "cross-site request refused")
    settings: Settings = request.app.state.settings
    allowed = {
        h.lower()
        for h in (
            request.headers.get("host"),
            request.headers.get("x-forwarded-host"),
            urlsplit(settings.public_url).netloc if settings.public_url else None,
        )
        if h
    }
    if urlsplit(origin).netloc.lower() not in allowed:
        raise HTTPException(403, "cross-site request refused")


def viewer_from(request: Request) -> Viewer:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(COOKIE)
    if not token or not owner_login_enabled(settings):
        return Viewer(is_owner=False)
    return Viewer(is_owner=_session_valid(settings, token))


def require_owner(request: Request) -> Viewer:
    v = viewer_from(request)
    if not v.is_owner:
        raise HTTPException(401, "sign in as the owner to do this")
    check_origin(request)
    return v


Owner = Annotated[Viewer, Depends(require_owner)]
CurrentViewer = Annotated[Viewer, Depends(viewer_from)]


class Me(BaseModel):
    is_owner: bool
    owner_login_enabled: bool


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def make_auth_router(throttle: LoginThrottle | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    throttle = throttle or LoginThrottle()

    @router.get("/me", response_model=Me)
    def me(request: Request, viewer: CurrentViewer, response: Response) -> Me:
        """Whether this browser is signed in as the owner, and whether owner sign-in exists."""
        response.headers["Cache-Control"] = "no-store"
        settings: Settings = request.app.state.settings
        return Me(is_owner=viewer.is_owner, owner_login_enabled=owner_login_enabled(settings))

    @router.post("/login", response_model=Me)
    def login(body: LoginIn, request: Request, response: Response) -> Me:
        settings: Settings = request.app.state.settings
        if not owner_login_enabled(settings):
            raise HTTPException(404, "owner sign-in is not set up on this server")
        check_origin(request)
        key = client_key(request)
        wait = throttle.retry_after(key)
        if wait > 0:
            raise HTTPException(
                429,
                f"too many wrong passwords; try again in {max(1, round(wait / 60))} min",
                headers={"Retry-After": str(round(wait) + 1)},
            )
        if not hmac.compare_digest(body.password.encode(), settings.owner_password.encode()):
            throttle.failed(key)
            log.warning("wrong owner password from %s", key)
            raise HTTPException(401, "wrong password")
        throttle.succeeded(key)
        token = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(days=SESSION_DAYS)
        conn = open_db(settings.database)  # the first sign-in may come before the worker
        try:
            with conn:
                conn.execute("DELETE FROM owner_sessions WHERE expires_at < ?", (now.isoformat(),))
                conn.execute(
                    "INSERT INTO owner_sessions (token_hash, created_at, expires_at) "
                    "VALUES (?, ?, ?)",
                    (_hash(token), now.isoformat(), expires.isoformat()),
                )
        finally:
            conn.close()
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_DAYS * 86400,
            path="/api",
            httponly=True,
            secure=settings.env == "prod",
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store"
        log.info("owner signed in from %s", key)
        return Me(is_owner=True, owner_login_enabled=True)

    @router.post("/logout", response_model=Me)
    def logout(request: Request, response: Response) -> Me:
        check_origin(request)
        settings: Settings = request.app.state.settings
        token = request.cookies.get(COOKIE)
        if token and settings.database.exists():
            conn = connect(settings.database)
            try:
                with conn:
                    conn.execute("DELETE FROM owner_sessions WHERE token_hash = ?", (_hash(token),))
            except sqlite3.OperationalError:
                pass  # no sessions table yet: nothing to end
            finally:
                conn.close()
        response.delete_cookie(COOKIE, path="/api")
        return Me(is_owner=False, owner_login_enabled=owner_login_enabled(settings))

    return router


def logout_all(conn: sqlite3.Connection) -> int:
    """End every owner session (after changing OWNER_PASSWORD). Returns how many."""
    with conn:
        return conn.execute("DELETE FROM owner_sessions").rowcount
