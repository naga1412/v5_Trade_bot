"""PR9 multi-entry order placement (Phase 5).

When dynamic_sizing.split_entries returns N > 1 tranches, the dispatcher
needs to place those N orders. Tranche 1 fires at market immediately;
tranches 2..N are placed as LIMIT orders at progressively-shifted DCA
prices (`entry_price ± dca_band_pct`) so Binance handles the deferred
fill when price moves against the signal direction.

Failure-isolation contract:
  - Tranche 1 failure → propagate the exception (we have nothing to
    track; let the dispatcher report blocked_error to the caller).
  - Tranche 2..N failure → log warning + continue. The earlier tranches
    are already on Binance; we leave them in place. The position is
    smaller than designed — not a safety issue.

SL+TP are managed at the position level by liquidation_monitor +
live_exit_monitor — multi_entry doesn't set them per-order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.exchanges.binance_live import (
    BinanceLiveClient,
    OrderRejected,
    OrderResult,
)


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MultiEntryResult:
    """What landed on Binance after place_multi_entry_orders returns."""
    tranches_placed: list[OrderResult]
    tranches_skipped: int  # tranches 2..N that failed; logged but non-fatal


def _dca_price(
    entry_price: float, direction: Literal["LONG", "SHORT"],
    dca_band_pct: float, tranche_idx: int,
) -> float:
    """Compute the LIMIT price for tranche N>0.

    For tranche_idx=1 (the second tranche, 0-indexed): one band-step
    against the signal. Higher indices: more steps against the signal,
    progressively cheaper-to-fill prices.

      LONG  signal at $100, dca_band=0.5%, tranche_idx=1
        → LIMIT BUY at $100 * (1 - 0.005) = $99.5
      SHORT signal at $100, dca_band=0.5%, tranche_idx=1
        → LIMIT SELL at $100 * (1 + 0.005) = $100.5
    """
    band = (dca_band_pct / 100.0) * tranche_idx
    if direction == "LONG":
        return entry_price * (1.0 - band)
    return entry_price * (1.0 + band)


async def place_multi_entry_orders(
    *,
    client: BinanceLiveClient,
    symbol: str,
    side: Literal["BUY", "SELL"],
    direction: Literal["LONG", "SHORT"],
    tranche_qtys: list[float],
    leverage: int,
    entry_price: float,
    dca_band_pct: float,
) -> MultiEntryResult:
    """Place N tranches: tranche 1 MARKET, tranches 2..N LIMIT at DCA prices.

    Returns MultiEntryResult listing what actually landed. Empty
    `tranches_placed` is only possible if tranche 1 itself raised
    (caller catches and reports blocked_error).
    """
    if not tranche_qtys:
        return MultiEntryResult(tranches_placed=[], tranches_skipped=0)

    placed: list[OrderResult] = []
    skipped = 0

    # Tranche 1: MARKET. Failure propagates.
    first = await client.place_order(
        symbol=symbol,
        side=side,
        quantity=tranche_qtys[0],
        leverage=leverage,
        order_type="MARKET",
    )
    placed.append(first)

    # Tranches 2..N: LIMIT at DCA prices. Failure logs + continues.
    for idx, qty in enumerate(tranche_qtys[1:], start=1):
        price = _dca_price(entry_price, direction, dca_band_pct, idx)
        try:
            tranche = await client.place_order(
                symbol=symbol,
                side=side,
                quantity=qty,
                leverage=leverage,
                order_type="LIMIT",
                price=price,
            )
            placed.append(tranche)
        except OrderRejected as e:
            skipped += 1
            log.warning(
                "place_multi_entry_orders: tranche %d/%d for %s qty=%s "
                "price=%.4f rejected: %s (continuing with %d tranche(s) placed)",
                idx + 1, len(tranche_qtys), symbol, qty, price, e, len(placed),
            )
        except Exception as e:  # noqa: BLE001
            skipped += 1
            log.error(
                "place_multi_entry_orders: tranche %d/%d for %s qty=%s "
                "failed: %s (continuing with %d tranche(s) placed)",
                idx + 1, len(tranche_qtys), symbol, qty, e, len(placed),
            )

    return MultiEntryResult(tranches_placed=placed, tranches_skipped=skipped)
