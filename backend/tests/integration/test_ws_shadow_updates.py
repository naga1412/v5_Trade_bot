"""Tests for the typed shadow_updates WebSocket channel publishers.

The publishers in app/ws/shadow_updates.py wrap manager.publish() with one
function per spec event type so the wire format is documented in one place.

These tests cover:
- The 4 publish helpers each call manager.publish with the right channel,
  key, and a payload that includes a "type" discriminator and the spec
  fields (with datetimes serialized as ISO strings).
- An end-to-end check that uses the real ConnectionManager + a FakeSocket
  to verify a published message reaches a subscribed client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.ws import shadow_updates
from app.ws.manager import ConnectionManager, Subscription


class FakeSocket:
    """Minimal WebSocketLike used to capture published messages."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, data: dict[str, Any]) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


# --- Module-level constants --------------------------------------------------


def test_channel_constant_is_shadow_updates() -> None:
    assert shadow_updates.CHANNEL == "shadow_updates"


# --- publish_position_opened -------------------------------------------------


@pytest.mark.asyncio
async def test_publish_position_opened_calls_manager_with_typed_payload() -> None:
    manager = AsyncMock(spec=ConnectionManager)
    opened_at = datetime(2026, 5, 11, 11, 30, tzinfo=UTC)

    await shadow_updates.publish_position_opened(
        manager,
        symbol="BTCUSDT",
        direction="LONG",
        entry_price=200.5,
        stop_loss=195.0,
        take_profit=215.0,
        signal_id="abc123",
        score=0.85,
        confidence=0.80,
        opened_at=opened_at,
    )

    manager.publish.assert_awaited_once()
    kwargs = manager.publish.await_args.kwargs
    assert kwargs["channel"] == "shadow_updates"
    assert kwargs["key"] == {"symbol": "BTCUSDT"}
    payload = kwargs["payload"]
    assert payload["type"] == "shadow_position_opened"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["direction"] == "LONG"
    assert payload["entry_price"] == 200.5
    assert payload["sl"] == 195.0
    assert payload["tp"] == 215.0
    assert payload["signal_id"] == "abc123"
    assert payload["score"] == 0.85
    assert payload["confidence"] == 0.80
    assert payload["opened_at"] == opened_at.isoformat()


# --- publish_position_closed -------------------------------------------------


@pytest.mark.asyncio
async def test_publish_position_closed_calls_manager_with_typed_payload() -> None:
    manager = AsyncMock(spec=ConnectionManager)
    closed_at = datetime(2026, 5, 11, 13, tzinfo=UTC)

    await shadow_updates.publish_position_closed(
        manager,
        symbol="ETHUSDT",
        direction="SHORT",
        entry_price=3000.0,
        exit_price=2950.0,
        exit_reason="TAKE_PROFIT",
        pnl_pct=1.667,
        pnl_usdt=0.50,
        bars_held=4,
        signal_id="sig9",
        closed_at=closed_at,
    )

    manager.publish.assert_awaited_once()
    kwargs = manager.publish.await_args.kwargs
    assert kwargs["channel"] == "shadow_updates"
    assert kwargs["key"] == {"symbol": "ETHUSDT"}
    payload = kwargs["payload"]
    assert payload["type"] == "shadow_position_closed"
    assert payload["symbol"] == "ETHUSDT"
    assert payload["direction"] == "SHORT"
    assert payload["entry_price"] == 3000.0
    assert payload["exit_price"] == 2950.0
    assert payload["exit_reason"] == "TAKE_PROFIT"
    assert payload["pnl_pct"] == 1.667
    assert payload["pnl_usdt"] == 0.50
    assert payload["bars_held"] == 4
    assert payload["signal_id"] == "sig9"
    assert payload["closed_at"] == closed_at.isoformat()


# --- publish_pnl_tick --------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_pnl_tick_calls_manager_with_typed_payload() -> None:
    manager = AsyncMock(spec=ConnectionManager)

    await shadow_updates.publish_pnl_tick(
        manager,
        symbol="SOLUSDT",
        current_price=152.4,
        unrealized_pnl_pct=-0.75,
    )

    manager.publish.assert_awaited_once()
    kwargs = manager.publish.await_args.kwargs
    assert kwargs["channel"] == "shadow_updates"
    assert kwargs["key"] == {"symbol": "SOLUSDT"}
    payload = kwargs["payload"]
    assert payload == {
        "type": "shadow_pnl_tick",
        "symbol": "SOLUSDT",
        "current_price": 152.4,
        "unrealized_pnl_pct": -0.75,
    }


# --- publish_universe_refreshed ---------------------------------------------


@pytest.mark.asyncio
async def test_publish_universe_refreshed_omits_symbol_from_key() -> None:
    manager = AsyncMock(spec=ConnectionManager)
    snapshot_at = datetime(2026, 5, 3, 0, tzinfo=UTC)

    await shadow_updates.publish_universe_refreshed(
        manager,
        snapshot_at=snapshot_at,
        top_30=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        removed_from_top_30=["FOOUSDT"],
    )

    manager.publish.assert_awaited_once()
    kwargs = manager.publish.await_args.kwargs
    assert kwargs["channel"] == "shadow_updates"
    # universe events are not symbol-scoped; key must be empty so they
    # broadcast to anyone subscribed to the channel.
    assert kwargs["key"] == {}
    payload = kwargs["payload"]
    assert payload["type"] == "universe_refreshed"
    assert payload["snapshot_at"] == snapshot_at.isoformat()
    assert payload["top_30"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert payload["removed_from_top_30"] == ["FOOUSDT"]


# --- End-to-end through the real ConnectionManager --------------------------


@pytest.mark.asyncio
async def test_position_opened_routes_through_real_manager_to_subscriber() -> None:
    """Drive the real ConnectionManager with a FakeSocket end-to-end.

    This verifies the wire format the frontend will see: a single message
    with channel == "shadow_updates" and payload["type"] set, routed to
    a client subscribed to {"symbol": "BTCUSDT"}.
    """
    mgr = ConnectionManager()
    sock = FakeSocket()
    mgr.attach(
        Subscription(client_id="c1", channel="shadow_updates",
                     params={"symbol": "BTCUSDT"}),
        sock,
    )

    opened_at = datetime(2026, 5, 11, 11, 30, tzinfo=UTC)
    await shadow_updates.publish_position_opened(
        mgr,
        symbol="BTCUSDT",
        direction="LONG",
        entry_price=200.5,
        stop_loss=195.0,
        take_profit=215.0,
        signal_id="abc123",
        score=0.85,
        confidence=0.80,
        opened_at=opened_at,
    )

    assert len(sock.sent) == 1
    msg = sock.sent[0]
    assert msg["channel"] == "shadow_updates"
    assert msg["key"] == {"symbol": "BTCUSDT"}
    assert msg["payload"]["type"] == "shadow_position_opened"
    assert msg["payload"]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_universe_refreshed_broadcasts_to_symbol_scoped_subscriber() -> None:
    """A universe refresh has no symbol; it must reach a client subscribed
    with a specific symbol filter (since the empty publish key trivially
    matches any subscription params).
    """
    mgr = ConnectionManager()
    sock = FakeSocket()
    mgr.attach(
        Subscription(client_id="c1", channel="shadow_updates",
                     params={"symbol": "BTCUSDT"}),
        sock,
    )

    snapshot_at = datetime(2026, 5, 3, 0, tzinfo=UTC)
    await shadow_updates.publish_universe_refreshed(
        mgr,
        snapshot_at=snapshot_at,
        top_30=["BTCUSDT"],
        removed_from_top_30=[],
    )

    assert len(sock.sent) == 1
    msg = sock.sent[0]
    assert msg["channel"] == "shadow_updates"
    assert msg["payload"]["type"] == "universe_refreshed"


@pytest.mark.asyncio
async def test_publish_does_not_reach_other_channel_subscribers() -> None:
    """A subscriber on a different channel must not see shadow_updates traffic."""
    mgr = ConnectionManager()
    sock = FakeSocket()
    mgr.attach(
        Subscription(client_id="c1", channel="live_prediction",
                     params={"symbol": "BTCUSDT", "timeframe": "1h"}),
        sock,
    )

    await shadow_updates.publish_pnl_tick(
        mgr, symbol="BTCUSDT", current_price=100.0, unrealized_pnl_pct=0.0,
    )

    assert sock.sent == []
