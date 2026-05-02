import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.manager import ConnectionManager, Subscription

router = APIRouter(prefix="/ws/v1", tags=["ws"])

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


@router.websocket("/{client_id}")
async def ws_endpoint(ws: WebSocket, client_id: str) -> None:
    await ws.accept()
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
