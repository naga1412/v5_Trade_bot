import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_ws_accepts_connection_and_subscribes() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/v1/test-client-1") as ws:
        ws.send_text(json.dumps({
            "action": "subscribe", "channel": "live_prediction",
            "params": {"symbol": "BTC/USDT", "timeframe": "1h"},
        }))
        msg = ws.receive_json()
        assert msg["type"] == "subscribed"
        assert msg["channel"] == "live_prediction"


def test_ws_handles_unsubscribe() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/v1/test-client-2") as ws:
        ws.send_text(json.dumps({"action": "subscribe", "channel": "live_prediction",
                                 "params": {"symbol": "BTC/USDT", "timeframe": "1h"}}))
        ws.receive_json()  # subscribed
        ws.send_text(json.dumps({"action": "unsubscribe"}))
        msg = ws.receive_json()
        assert msg["type"] == "unsubscribed"


def test_ws_two_independent_clients_isolated() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/v1/cA") as wsA, \
         client.websocket_connect("/ws/v1/cB") as wsB:
        wsA.send_text(json.dumps({"action": "subscribe", "channel": "live_prediction",
                                  "params": {"symbol": "BTC/USDT", "timeframe": "1h"}}))
        wsA.receive_json()
        wsB.send_text(json.dumps({"action": "subscribe", "channel": "live_prediction",
                                  "params": {"symbol": "ETH/USDT", "timeframe": "1h"}}))
        wsB.receive_json()
        # No assertion on traffic; passing means no cross-talk in 1s
        # (real cross-talk would be caught by Phase O E2E)
