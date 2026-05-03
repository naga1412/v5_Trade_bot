"""Typed publishers for the shadow_updates WebSocket channel.

All shadow trading events flow through these functions so the wire format
is documented in one place and easy to evolve. Every payload includes a
``"type"`` discriminator (one of the ``EventType`` literals below) so the
frontend can route on a single field.

Wire format (matches ConnectionManager.publish):

    {"channel": "shadow_updates", "key": {...}, "payload": {"type": ..., ...}}

For per-symbol events the key is ``{"symbol": <symbol>}``; for broadcasts
(``universe_refreshed``) the key is empty so any subscriber to the channel
receives the message regardless of their symbol filter.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.ws.manager import ConnectionManager

CHANNEL = "shadow_updates"

EventType = Literal[
    "shadow_position_opened",
    "shadow_position_closed",
    "shadow_pnl_tick",
    "universe_refreshed",
]


async def _publish(
    manager: ConnectionManager,
    *,
    event_type: EventType,
    symbol: str | None,
    payload: dict[str, Any],
) -> None:
    """Internal helper — the only place that talks to ConnectionManager."""
    key: dict[str, Any] = {"symbol": symbol} if symbol is not None else {}
    await manager.publish(
        channel=CHANNEL,
        key=key,
        payload={"type": event_type, **payload},
    )


async def publish_position_opened(
    manager: ConnectionManager,
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    signal_id: str,
    score: float,
    confidence: float,
    opened_at: datetime,
) -> None:
    """Emit a ``shadow_position_opened`` event for ``symbol``."""
    await _publish(
        manager,
        event_type="shadow_position_opened",
        symbol=symbol,
        payload={
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "sl": stop_loss,
            "tp": take_profit,
            "signal_id": signal_id,
            "score": score,
            "confidence": confidence,
            "opened_at": opened_at.isoformat(),
        },
    )


async def publish_position_closed(
    manager: ConnectionManager,
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    pnl_pct: float,
    pnl_usdt: float,
    bars_held: int,
    signal_id: str,
    closed_at: datetime,
) -> None:
    """Emit a ``shadow_position_closed`` event for ``symbol``."""
    await _publish(
        manager,
        event_type="shadow_position_closed",
        symbol=symbol,
        payload={
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "bars_held": bars_held,
            "signal_id": signal_id,
            "closed_at": closed_at.isoformat(),
        },
    )


async def publish_pnl_tick(
    manager: ConnectionManager,
    *,
    symbol: str,
    current_price: float,
    unrealized_pnl_pct: float,
) -> None:
    """Emit a ``shadow_pnl_tick`` event for an open ``symbol`` position."""
    await _publish(
        manager,
        event_type="shadow_pnl_tick",
        symbol=symbol,
        payload={
            "symbol": symbol,
            "current_price": current_price,
            "unrealized_pnl_pct": unrealized_pnl_pct,
        },
    )


async def publish_universe_refreshed(
    manager: ConnectionManager,
    *,
    snapshot_at: datetime,
    top_30: list[str],
    removed_from_top_30: list[str],
) -> None:
    """Emit a ``universe_refreshed`` broadcast (no symbol scope)."""
    await _publish(
        manager,
        event_type="universe_refreshed",
        symbol=None,
        payload={
            "snapshot_at": snapshot_at.isoformat(),
            "top_30": top_30,
            "removed_from_top_30": removed_from_top_30,
        },
    )


__all__ = [
    "CHANNEL",
    "EventType",
    "publish_pnl_tick",
    "publish_position_closed",
    "publish_position_opened",
    "publish_universe_refreshed",
]
