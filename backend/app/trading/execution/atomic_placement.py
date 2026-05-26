"""PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP (2026-05-26): atomic market + SL + TP
placement with emergency close on partial failure.

ROOT CAUSE THIS FIXES
=====================
Pre-PR the placement flow was:

    1. client.place_order(MARKET)                # Binance fills, position open
    2. build_live_trade_payload(...)              # may raise
    3. insert_with_chain(session, ...)            # may raise

If step 2 or 3 raised after step 1 succeeded, the Binance position was
left open with NO bot record. live_exit_monitor couldn't see it; no SL
or TP was ever placed (the bot never placed Binance-native SL/TP — the
`stop_loss_price` / `take_profit_price` kwargs on `place_order` were
silently ignored). Operator had to close manually to prevent unbounded
loss. Observed at least three orphans on 2026-05-25 from CheckViolation
rollbacks (PR-FIX-PR264 era) and another on 2026-05-26.

THIS HELPER
===========
``place_with_sltp`` orchestrates the three Binance orders so they
either ALL succeed (returning the three order IDs) or the position
is emergency-closed back to flat:

    1. Place MARKET entry → if fails, no position, raise.
    2. Place STOP_MARKET (closePosition=true) → if fails, emergency
       close the just-opened position, raise.
    3. Place TAKE_PROFIT_MARKET (closePosition=true) → if fails,
       cancel the SL we just placed (idempotent — it may have already
       fired in the race window), emergency close the position, raise.

Emergency close itself uses ``close_position`` which issues a
reduce-only MARKET on the opposite side. If THAT fails (catastrophic),
the helper raises with the full state in the message so the operator
can intervene; the orphan is now visible because the caller will mark
``live_trades.status='failed'`` with the failure_reason text and
``live_exit_monitor``'s reconciler will see it.

CALLER CONTRACT
===============
Caller pre-INSERTs a ``live_trades`` row with ``status='pending'``,
calls ``place_with_sltp``, then UPDATEs the row to either
``status='open'`` (with the three order IDs) or ``status='failed'``
(with the failure_reason). Two separate sessions because Binance can
take seconds and we don't want to hold a DB transaction that long.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.exchanges.binance_live import (
    BinanceLiveClient,
    BinanceLiveError,
    OrderRejected,
    OrderResult,
)


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlacementResult:
    """Successful triple-order outcome."""

    entry_order: OrderResult
    sl_order_id: str
    tp_order_id: str


class GhostPositionError(BinanceLiveError):
    """Raised when atomic placement failed AND the emergency close ALSO
    failed. The caller MUST mark live_trades.status='failed' with the
    full message so reconciliation can see the orphan and the operator
    can intervene. This is the worst-case state — a Binance position
    exists, has no SL/TP attached, and the bot couldn't close it.
    """


def _close_side_for(entry_side: Literal["BUY", "SELL"]) -> Literal["BUY", "SELL"]:
    """LONG entry was BUY → close with SELL; SHORT entry was SELL → close BUY."""
    return "SELL" if entry_side == "BUY" else "BUY"


async def place_with_sltp(
    client: BinanceLiveClient,
    *,
    symbol: str,
    side: Literal["BUY", "SELL"],
    quantity: float,
    leverage: int,
    stop_loss_price: float,
    take_profit_price: float,
) -> PlacementResult:
    """Atomically place market entry + Binance-native SL + Binance-native TP.

    On full success: returns PlacementResult with the three order IDs.
    On any failure after the market entry: attempts emergency close,
    raises OrderRejected/BinanceLiveError with the original cause.
    On emergency close ALSO failing: raises GhostPositionError so the
    caller marks status='failed' and reconciliation flags the orphan.

    ``symbol`` is Binance-native (e.g. "BTCUSDT", not "BTC/USDT").
    ``side`` is the ENTRY side ("BUY" for LONG, "SELL" for SHORT).
    """
    close_side = _close_side_for(side)

    # ─── Step 1: market entry ──────────────────────────────────────────
    entry_order = await client.place_order(
        symbol=symbol, side=side, quantity=quantity,
        leverage=leverage, order_type="MARKET",
    )
    log.info(
        "place_with_sltp: market entry filled %s %s %.6f@%.4f (orderId=%s)",
        symbol, side, entry_order.qty, entry_order.avg_fill_price,
        entry_order.binance_order_id,
    )

    # ─── Step 2: SL ────────────────────────────────────────────────────
    try:
        sl_order = await client.place_stop_loss_close(
            symbol=symbol, close_side=close_side,
            stop_price=stop_loss_price,
        )
    except (OrderRejected, BinanceLiveError) as sl_exc:
        log.error(
            "place_with_sltp: SL placement FAILED for %s @ %.4f — "
            "emergency closing position: %s",
            symbol, stop_loss_price, sl_exc,
        )
        await _emergency_close(client, symbol=symbol, after="SL placement failed", original=sl_exc)
        raise

    log.info(
        "place_with_sltp: SL armed %s @ %.4f (orderId=%s)",
        symbol, stop_loss_price, sl_order.binance_order_id,
    )

    # ─── Step 3: TP ────────────────────────────────────────────────────
    try:
        tp_order = await client.place_take_profit_close(
            symbol=symbol, close_side=close_side,
            stop_price=take_profit_price,
        )
    except (OrderRejected, BinanceLiveError) as tp_exc:
        log.error(
            "place_with_sltp: TP placement FAILED for %s @ %.4f — "
            "cancelling SL %s and emergency closing position: %s",
            symbol, take_profit_price, sl_order.binance_order_id, tp_exc,
        )
        # Cancel the SL we just placed. Idempotent — if the SL already
        # triggered + filled in the race window, we proceed to the
        # emergency close which will detect the flat position.
        try:
            await client.cancel_order_idempotent(
                symbol=symbol, order_id=sl_order.binance_order_id,
            )
        except Exception as cancel_exc:  # noqa: BLE001
            # Cancel itself failed — log + continue to emergency close.
            # We don't want a cancel-permission edge case to leave the
            # caller without ANY cleanup attempt.
            log.warning(
                "place_with_sltp: SL cancel failed (continuing to "
                "emergency close): %s", cancel_exc,
            )
        await _emergency_close(client, symbol=symbol, after="TP placement failed", original=tp_exc)
        raise

    log.info(
        "place_with_sltp: TP armed %s @ %.4f (orderId=%s)",
        symbol, take_profit_price, tp_order.binance_order_id,
    )
    return PlacementResult(
        entry_order=entry_order,
        sl_order_id=sl_order.binance_order_id,
        tp_order_id=tp_order.binance_order_id,
    )


async def _emergency_close(
    client: BinanceLiveClient,
    *,
    symbol: str,
    after: str,
    original: Exception,
) -> None:
    """Last-line defense: close whatever position is open on the symbol.

    Called when SL or TP placement failed AFTER the market entry. We
    can't safely leave the position open without risk management.

    On success: logs and returns; the caller's outer raise propagates
    the original SL/TP error.
    On failure: raises GhostPositionError with the full state so the
    caller stores failure_reason verbatim. live_exit_monitor's
    reconciler picks up the live_trades.status='failed' row and the
    operator gets alerted.
    """
    try:
        result = await client.close_position(symbol=symbol)
    except BinanceLiveError as close_exc:
        # Position may already be flat — close_position raises
        # BinanceLiveError("no open position") in that case. That's
        # actually GOOD: somehow the position closed itself (SL fired,
        # liquidation, manual close in race). We don't want to treat
        # that as catastrophic.
        if "no open position" in str(close_exc).lower():
            log.warning(
                "_emergency_close(%s): position already flat (likely "
                "SL fired during failed TP race). after=%s",
                symbol, after,
            )
            return
        # Real close failure — orphan exists, operator MUST intervene.
        raise GhostPositionError(
            f"CATASTROPHIC: {after}, AND emergency close failed for "
            f"{symbol}. Original error: {original}. Close error: "
            f"{close_exc}. Position likely still open on Binance with "
            "no SL/TP attached. OPERATOR MUST MANUALLY CLOSE."
        ) from close_exc
    log.warning(
        "_emergency_close(%s): closed (orderId=%s, qty=%.6f); after=%s",
        symbol, result.binance_order_id, result.qty, after,
    )


__all__ = [
    "GhostPositionError",
    "PlacementResult",
    "place_with_sltp",
]
