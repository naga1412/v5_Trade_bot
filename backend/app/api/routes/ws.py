import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.config import get_settings
from app.deps import CFAccessConfig, CFAccessError, verify_cf_access_jwt
from app.ws.manager import ConnectionManager, Subscription

router = APIRouter(prefix="/ws/v1", tags=["ws"])

log = logging.getLogger(__name__)
manager = ConnectionManager()
HEARTBEAT_SECONDS = 15.0


async def _heartbeat_loop(ws: WebSocket) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            await ws.send_json({"type": "ping",
                                "ts": datetime.now(timezone.utc).isoformat()})
        except Exception:  # noqa: BLE001
            return


async def _authenticate_ws(ws: WebSocket) -> str | None:
    """Verify the CF Access JWT on the WS upgrade if one is present.

    Cloudflare Access gates WebSocket upgrades at the tunnel layer —
    requests that arrive at this handler have already cleared CF
    Access's policy check. Browsers cannot set arbitrary headers on
    WebSocket upgrades, so the Cf-Access-Jwt-Assertion header is
    NOT reliably forwarded for WS the way it is for REST.

    Strategy: when a JWT is present, verify it as defence-in-depth.
    When no JWT is present in non-dev mode, accept the connection on
    the basis that CF Access already gated it (same model the REST
    handlers depend on for their auth headers, just without the
    second verification because the browser can't set the header).

    A truly malicious client with direct access to the backend port
    could bypass this — but the same is true of every endpoint, and
    the production deployment binds 8000 to 127.0.0.1 only, so only
    Cloudflare's tunnel can reach it.
    """
    settings = get_settings()
    if settings.env == "development":
        return "dev@local"
    jwt = ws.headers.get("cf-access-jwt-assertion", "").strip()
    if not jwt:
        # No header — most likely a browser WS upgrade that CF Access
        # gated at the tunnel but couldn't forward the JWT for. Accept.
        return "cf-access-tunnel"
    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        log.error("ws: CF Access env vars not configured; refusing")
        await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        return None
    try:
        user = await verify_cf_access_jwt(
            jwt,
            cfg=CFAccessConfig(
                team_domain=settings.cf_access_team_domain,
                aud=settings.cf_access_aud,
            ),
        )
    except CFAccessError as e:
        log.warning("ws: CF Access JWT rejected: %s", e)
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return None
    return user.email or user.sub or "unknown"


@router.websocket("/{client_id}")
async def ws_endpoint(ws: WebSocket, client_id: str) -> None:
    await ws.accept()
    user = await _authenticate_ws(ws)
    if user is None:
        return  # _authenticate_ws already closed the socket
    hb_task = asyncio.create_task(_heartbeat_loop(ws))
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")
            channel = msg.get("channel")
            params = msg.get("params") or {}

            if action == "subscribe" and channel:
                manager.attach(Subscription(client_id, channel, params), ws)
                await ws.send_json({"type": "subscribed",
                                    "channel": channel, "params": params})
            elif action == "unsubscribe":
                manager.detach(client_id)
                await ws.send_json({"type": "unsubscribed"})
            elif action == "pong":
                pass
            else:
                await ws.send_json({"type": "error", "reason": "unknown action"})
    except WebSocketDisconnect:
        pass
    finally:
        hb_task.cancel()
        manager.detach(client_id)
