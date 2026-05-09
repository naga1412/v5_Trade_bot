"""SP-9 Phase A — WebSocket auth gate tests.

Verifies that the /ws/v1/{client_id} endpoint enforces CF Access JWT
in non-dev environments. The pre-existing test_ws_reconnect.py covers
the dev-mode bypass path; this file covers the auth-required paths.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def _force_prod(env_value: str = "production"):
    """Patch get_settings() so the WS handler enters the auth path."""
    from app import deps as deps_mod
    from app.api.routes import ws as ws_mod
    from app.config import Settings

    base = ws_mod.get_settings()
    overridden = Settings(
        env=env_value,
        log_level=base.log_level,
        database_url=base.database_url,
        redis_url=base.redis_url,
        cf_access_team_domain="example.cloudflareaccess.com",
        cf_access_aud="aud-x",
        master_passphrase=base.master_passphrase,
    )

    def _patched_get_settings():
        return overridden

    return [
        patch.object(ws_mod, "get_settings", _patched_get_settings),
        patch.object(deps_mod, "get_settings", _patched_get_settings),
    ]


def test_ws_rejects_connection_without_jwt_in_prod() -> None:
    patches = _force_prod()
    for p in patches:
        p.start()
    try:
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/v1/c1") as ws:
                # Server should close immediately after .accept(); reading
                # any message raises WebSocketDisconnect.
                ws.receive_json()
    finally:
        for p in patches:
            p.stop()


def test_ws_rejects_connection_with_bad_jwt_in_prod() -> None:
    patches = _force_prod()
    for p in patches:
        p.start()
    try:
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/v1/c2",
                headers={"cf-access-jwt-assertion": "not-a-real-jwt"},
            ) as ws:
                ws.receive_json()
    finally:
        for p in patches:
            p.stop()


def test_ws_dev_mode_still_works_without_jwt() -> None:
    """Regression — the dev-bypass keeps the LAN dashboard unblocked."""
    import json
    client = TestClient(app)
    with client.websocket_connect("/ws/v1/dev-client") as ws:
        ws.send_text(json.dumps({
            "action": "subscribe", "channel": "live_prediction",
            "params": {"symbol": "BTC/USDT", "timeframe": "1h"},
        }))
        msg = ws.receive_json()
        assert msg["type"] == "subscribed"
