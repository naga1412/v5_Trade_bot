"""SP-PAUSE request middleware.

When ``pause_state.is_paused()`` is True, every HTTP request is gated
through :func:`_is_allowed_when_paused`. Allow-listed requests pass
through; everything else returns 423 Locked with::

    {"detail": "system_paused", "since": "<ISO-8601 or null>"}

Allow-list rules (spec §3.4 + decisions §6 row 5/6):

* Path prefixes that are *always* allowed regardless of method:
    - ``/api/v1/admin/`` (admin needs to be able to resume)
    - ``/api/v1/health`` (Cloudflare Tunnel health probes)
    - ``/api/v1/me/`` (user can still see + edit their own profile)
    - ``/api/v1/bot-status/`` (read-only review pages)
    - ``/api/v1/ws/`` (WebSocket upgrade — open already, resume instant)
    - ``/metrics`` (Prometheus scrape)
* Method ``OPTIONS`` is always allowed (CORS preflight).
* GET-only allow-list:
    - ``/api/v1/predictions/list``
    - ``/api/v1/shadow_trades/list``
    - ``/`` (SPA root)
    - ``/assets/``, ``/static/``, ``/favicon.ico``, ``/index.html``,
      ``/vite.svg`` (frontend bundle paths)

Anything not on either list → 423.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.ops import pause_state


log = logging.getLogger(__name__)

# Always allowed path prefixes (any HTTP method).
_ALWAYS_ALLOW_PREFIXES: tuple[str, ...] = (
    "/api/v1/admin/",
    "/api/v1/health",
    "/api/v1/me/",
    "/api/v1/bot-status/",
    "/api/v1/ws/",
    "/metrics",
)

# GET-only allow-list (full path or prefix).
_GET_ONLY_PATHS: tuple[str, ...] = (
    "/api/v1/predictions/list",
    "/api/v1/shadow_trades/list",
    "/",
    "/index.html",
    "/favicon.ico",
    "/vite.svg",
)
_GET_ONLY_PREFIXES: tuple[str, ...] = (
    "/assets/",
    "/static/",
)


def _is_allowed_when_paused(request: Any) -> bool:
    method = request.method.upper()
    if method == "OPTIONS":
        return True
    path = request.url.path
    for prefix in _ALWAYS_ALLOW_PREFIXES:
        if path.startswith(prefix):
            return True
    if method == "GET":
        if path in _GET_ONLY_PATHS:
            return True
        for prefix in _GET_ONLY_PREFIXES:
            if path.startswith(prefix):
                return True
    return False


def register_pause_middleware(app: FastAPI) -> None:
    """Wire the pause middleware onto an app instance.

    Called from :func:`app.main.create_app` *before* :func:`instrument_app`
    so Prometheus continues to observe the 423 path.
    """

    @app.middleware("http")
    async def _pause_middleware(  # type: ignore[no-untyped-def]
        request: Request, call_next,
    ):
        try:
            paused = await pause_state.is_paused()
        except Exception:  # noqa: BLE001
            log.warning("pause_middleware: pause_state read failed; passing through")
            paused = False
        if paused and not _is_allowed_when_paused(request):
            since: str | None = None
            try:
                state = await pause_state.get_state()
                if state.since is not None:
                    since = state.since.isoformat()
            except Exception:  # noqa: BLE001
                since = None
            return JSONResponse(
                status_code=423,
                content={"detail": "system_paused", "since": since},
            )
        return await call_next(request)


__all__ = [
    "_is_allowed_when_paused",
    "register_pause_middleware",
]
