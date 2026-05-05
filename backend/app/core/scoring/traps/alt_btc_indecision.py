"""alt_btc_indecision trap — alt + BTC consolidating (high, both sides).

When BTC is consolidating (low ATR%) and the symbol is an alt, the alt is
likely to whip on any BTC move. Fires when:
  - context.symbol != "BTC/USDT" (or the BTC perpetual variants)
  - context.btc_atr_pct is not None and < 0.5
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_BTC_ATR_PCT_THRESHOLD: float = 0.5
_BTC_SYMBOLS: frozenset[str] = frozenset({"BTC/USDT", "BTC/USD", "BTCUSDT", "BTCUSD"})


class AltBtcIndecisionTrap:
    """Fire on alt symbols when BTC is consolidating (ATR% < 0.5)."""

    trap_id: str = "alt_btc_indecision"
    severity: Severity = "high"
    side: Side = "both"

    def check(
        self,
        bars: pd.DataFrame,
        *,
        current_idx: int,
        layer_scores: dict[int, LayerScore | None],
        proposed_direction: Direction,
        context: TrapContext,
    ) -> TrapFire | None:
        if not context.symbol or context.symbol in _BTC_SYMBOLS:
            return None
        if context.btc_atr_pct is None:
            return None
        if context.btc_atr_pct >= _BTC_ATR_PCT_THRESHOLD:
            return None
        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"alt {context.symbol} with BTC ATR%={context.btc_atr_pct:.3f} "
                f"< {_BTC_ATR_PCT_THRESHOLD} (BTC consolidating)"
            ),
            evidence={
                "symbol": context.symbol,
                "btc_atr_pct": context.btc_atr_pct,
                "threshold": _BTC_ATR_PCT_THRESHOLD,
            },
        )
