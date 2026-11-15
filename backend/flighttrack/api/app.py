"""FastAPI app: JSON under /api, plus the page (M5: the old static page; M6: the
built frontend with an SPA fallback). Copied from Snow Watch's api/app.py.

Run by `uvicorn --factory flighttrack.api.app:production_app`. The API only
reads prices; it writes what is tracked, jobs and owner sessions. The worker
(`flighttrack worker`) does all the collecting.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from .. import __version__, watches
from .. import config as config_mod
from .. import html as html_mod
from ..config import Config
from ..settings import Settings, get_settings
from ..store.db import connect, migrate
from .admin import make_admin_router
from .auth import MIN_PASSWORD, make_auth_router, owner_login_enabled
from .models import LivezOut, MetaOut
from .public import make_router
from .ratelimit import LoginThrottle, RateLimiter

log = logging.getLogger("flighttrack.api")
access = logging.getLogger("flighttrack.access")

# Public reads: 60 a minute per client; the airport search: 30 a minute.
READS_PER_MINUTE = 60
AIRPORTS_PER_MINUTE = 30
# The interim page (until M6) is drawn again when it is older than this.
PAGE_MAX_AGE_S = 60.0
ROBOTS = "User-agent: *\nDisallow: /\n"


def _sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def inline_script_hashes(index: Path) -> list[str]:
    """CSP hashes of the inline scripts in the built index.html (SvelteKit's start-up code)."""
    if not index.exists():
        return []
    html = index.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
    return [_sha256(s) for s in scripts if s.strip()]


def content_security_policy(script_hashes: list[str]) -> str:
    """What the page may load: only this site. No third parties at all."""
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' " + " ".join(script_hashes),
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "manifest-src 'self'",
            "worker-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def readable(exc: RequestValidationError) -> str:
    """Pydantic's errors as one sentence: `origin: XYZ is not an airport; name: required`."""
    parts = []
    for err in exc.errors():
        loc = [str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path")]
        msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
        if msg == "Field required":
            msg = "required"
        parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
    return "; ".join(parts) or "invalid request"


def create_app(
    settings: Settings | None = None,
    *,
    cfg: Config | None = None,
    throttle: LoginThrottle | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    cfg = cfg or config_mod.load(strict_secrets=False, settings=settings)
    prod = settings.env == "prod"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Bring an existing database up to date (the worker does the same at its
        # start; migrations are idempotent). A missing one is left to the worker.
        if settings.database.exists():
            try:
                conn = connect(settings.database)
                try:
                    applied = migrate(conn)
                finally:
                    conn.close()
                if applied:
                    log.info("applied database migrations: %s", ", ".join(applied))
            except sqlite3.Error:
                log.exception("could not migrate %s", settings.database)
        yield

    app = FastAPI(
        title="Flight Watch",
        version=__version__,
        # The interactive API docs are for development; the public site doesn't need them.
        docs_url=None if prod else "/api/docs",
        openapi_url=None if prod else "/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.cfg = cfg

    static = settings.static_dir
    spa = static is not None and (static / "index.html").exists()
    hashes = inline_script_hashes(static / "index.html") if spa and static else []
    if not spa:
        hashes = [_sha256(html_mod.JS)]  # the interim page's one inline script
    security_headers = {
        "Content-Security-Policy": content_security_policy(hashes),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        # Public, but not meant for search results.
        "X-Robots-Tag": "noindex, nofollow",
    }
    if prod:
        security_headers["Strict-Transport-Security"] = "max-age=31536000"

    @app.middleware("http")
    async def add_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        request_id = request.headers.get("x-request-id", "")[:64] or secrets.token_hex(6)
        try:
            response = await call_next(request)
        except Exception:
            access.exception("%s %s failed [%s]", request.method, request.url.path, request_id)
            raise
        ms = (time.perf_counter() - started) * 1000
        # Errors, writes and slow requests at INFO; the steady stream of reads at DEBUG.
        level = (
            logging.INFO
            if response.status_code >= 400 or request.method != "GET" or ms > 2000
            else logging.DEBUG
        )
        access.log(
            level,
            "%s %s %d %.0f ms [%s]",
            request.method,
            request.url.path,
            response.status_code,
            ms,
            request_id,
        )
        response.headers["X-Request-ID"] = request_id
        for name, value in security_headers.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": readable(exc)}, status_code=422)

    @app.exception_handler(watches.NotFound)
    async def not_found(_: Request, exc: watches.NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(watches.WatchError)
    async def cannot_save(_: Request, exc: watches.WatchError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/livez", response_model=LivezOut)
    def livez() -> LivezOut:
        """The process is up. Used by the container health check."""
        return LivezOut(ok=True)

    @app.get("/api/meta", response_model=MetaOut)
    def meta() -> MetaOut:
        """Version, home airport and what is set up on this server."""
        return MetaOut(
            version=__version__,
            commit=settings.git_commit or None,
            home_airport=cfg.home,
            currency=cfg.search.currency,
            owner_login_enabled=owner_login_enabled(settings),
            push_enabled=bool(settings.vapid_public_key and settings.vapid_private_key),
        )

    @app.get("/robots.txt", include_in_schema=False)
    def robots() -> PlainTextResponse:
        return PlainTextResponse(ROBOTS, headers={"Cache-Control": "public, max-age=86400"})

    if settings.owner_password and len(settings.owner_password) < MIN_PASSWORD:
        log.error(
            "OWNER_PASSWORD is shorter than %d characters; owner sign-in is off", MIN_PASSWORD
        )
    app.include_router(make_auth_router(throttle))
    app.include_router(
        make_router(
            RateLimiter(rate=READS_PER_MINUTE / 60, burst=READS_PER_MINUTE),
            RateLimiter(rate=AIRPORTS_PER_MINUTE / 60, burst=AIRPORTS_PER_MINUTE),
        )
    )
    app.include_router(make_admin_router())

    if spa and static is not None:
        root = static.resolve()
        fresh = {"Cache-Control": "no-cache"}  # always check for a new version
        forever = {"Cache-Control": "public, max-age=31536000, immutable"}

        @app.get("/{path:path}", include_in_schema=False)
        def spa_files(path: str) -> FileResponse:
            if path.startswith("api/") or path == "api":
                raise HTTPException(status_code=404)
            target = (root / path).resolve()
            if path and target.is_file() and target.is_relative_to(root):
                # File names under _app/immutable carry a content hash.
                headers = forever if path.startswith("_app/immutable/") else fresh
                return FileResponse(target, headers=headers)
            return FileResponse(root / "index.html", headers=fresh)

    else:
        # Until the frontend exists (M6): the old static page, drawn from the
        # database at most once a minute.
        page: dict[str, tuple[float, str]] = {}

        @app.get("/", include_in_schema=False)
        def index() -> HTMLResponse:
            headers = {"Cache-Control": "no-cache"}
            hit = page.get("/")
            if hit is not None and time.monotonic() - hit[0] < PAGE_MAX_AGE_S:
                return HTMLResponse(hit[1], headers=headers)
            if not settings.database.exists():
                return HTMLResponse(
                    "<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
                    "<title>Flight Watch</title></head><body><p>Flight Watch is starting: "
                    "no prices yet.</p></body></html>",
                    status_code=503,
                    headers=headers,
                )
            conn = connect(settings.database, readonly=True)
            try:
                body = html_mod.render(conn, cfg=cfg)
            finally:
                conn.close()
            page["/"] = (time.monotonic(), body)
            return HTMLResponse(body, headers=headers)

    return app


def production_app() -> FastAPI:
    """What the server runs (`uvicorn --factory flighttrack.api.app:production_app`):
    the app with JSON-line logging."""
    from ..logs import setup_logging

    setup_logging()
    # Uvicorn's own lines (start-up, errors) go through the same JSON handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # our middleware logs
    return create_app()
