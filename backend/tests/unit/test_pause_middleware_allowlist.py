from typing import Any

import fakeredis.aioredis
import pytest

from app.api.pause_middleware import _is_allowed_when_paused
from app.ops import pause_state


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    monkeypatch.setattr(
        pause_state, "_get_redis",
        lambda: fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    yield
    pause_state._reset_for_tests()


def _req(method: str, path: str) -> Any:
    """Tiny stand-in for fastapi.Request — only .method/.url.path are read."""
    class _U:
        def __init__(self, p: str) -> None:
            self.path = p
    class _R:
        def __init__(self, m: str, p: str) -> None:
            self.method = m
            self.url = _U(p)
    return _R(method, path)


@pytest.mark.parametrize("method,path,expected", [
    # admin/* always allowed (admin must resume)
    ("POST", "/api/v1/admin/system/resume",       True),
    ("GET",  "/api/v1/admin/system/state",        True),
    ("POST", "/api/v1/admin/news/refresh",        True),
    # health always allowed (Cloudflare Tunnel)
    ("GET",  "/api/v1/health",                    True),
    # me/* always allowed
    ("GET",  "/api/v1/me/",                       True),
    ("PATCH","/api/v1/me/",                       True),
    # bot-status read-only allowed
    ("GET",  "/api/v1/bot-status/overview",       True),
    # metrics always allowed (Prometheus)
    ("GET",  "/metrics",                          True),
    # WS upgrade allowed
    ("GET",  "/api/v1/ws/live-prediction",        True),
    # OPTIONS for CORS preflight always allowed
    ("OPTIONS", "/api/v1/predict",                True),
    # GET predictions/shadow_trades list allowed
    ("GET",  "/api/v1/predictions/list",          True),
    ("GET",  "/api/v1/shadow_trades/list",        True),
    # Frontend root + assets allowed for GET only
    ("GET",  "/",                                 True),
    ("GET",  "/assets/index.js",                  True),
    ("GET",  "/static/main.css",                  True),
    ("GET",  "/favicon.ico",                      True),
    ("GET",  "/index.html",                       True),
    ("GET",  "/vite.svg",                         True),
    # NOT allowed
    ("POST", "/api/v1/predict",                   False),
    ("POST", "/api/v1/shadow_trades",             False),
    ("POST", "/api/v1/predictions/list",          False),
    ("POST", "/",                                 False),
    ("POST", "/assets/foo",                       False),
    ("GET",  "/api/v1/scanner",                   False),
    ("GET",  "/api/v1/intermarket/BTC%2FUSDT",    False),
])
def test_allowlist(method: str, path: str, expected: bool) -> None:
    assert _is_allowed_when_paused(_req(method, path)) is expected
