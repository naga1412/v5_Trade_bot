"""SP-8 Phase C — position sizing.

Two modes (spec §5):

1. Fixed dollar amount (default) — single value or random range.
2. Percentage of portfolio — strict ramp by # of successful real trades.

The functions are pure: they take the user settings and the current
portfolio + trade-count state and return the next trade's margin in
USDT. No DB access, no clock dependency.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal


SizingMode = Literal["fixed", "percent"]


@dataclass(frozen=True)
class FixedSizingConfig:
    """Single value, or uniform random range."""

    amount_usdt: float | None = None
    range_low_usdt: float | None = None
    range_high_usdt: float | None = None

    def resolve(self, *, rng: random.Random | None = None) -> float:
        """Return the margin USDT for the next trade.

        - amount_usdt set: return it.
        - range_low + range_high set: uniform random in [low, high].
        - both set: amount_usdt wins (caller config error is silent).
        """
        if self.amount_usdt is not None:
            return float(self.amount_usdt)
        if self.range_low_usdt is None or self.range_high_usdt is None:
            raise ValueError(
                "FixedSizingConfig requires either amount_usdt OR "
                "(range_low_usdt + range_high_usdt)"
            )
        lo = float(self.range_low_usdt)
        hi = float(self.range_high_usdt)
        if hi < lo:
            lo, hi = hi, lo
        r = rng or random.Random()
        return r.uniform(lo, hi)


# Spec §5.2 ramp — successful-trade count → fraction of portfolio.
# (Inclusive lower, exclusive upper.)
_PERCENT_RAMP: tuple[tuple[int, int, float], ...] = (
    (0, 30, 0.001),       # 0.1%
    (30, 100, 0.0025),    # 0.25%
    (100, 500, 0.005),    # 0.5%
    (500, 10**9, 0.01),   # 1.0% — the Half-Kelly cap
)


def percent_sizing_amount(
    portfolio_value_usdt: float, successful_trades: int,
) -> float:
    """Return the percent-mode margin for the next trade.

    Args:
        portfolio_value_usdt: current portfolio value (margin + free).
        successful_trades: count of trades that closed without gapping
            through stop-loss. Defines the ramp tier.
    """
    if portfolio_value_usdt <= 0 or successful_trades < 0:
        return 0.0
    for lo, hi, frac in _PERCENT_RAMP:
        if lo <= successful_trades < hi:
            return portfolio_value_usdt * frac
    # Defensive fallback: Half-Kelly cap.
    return portfolio_value_usdt * _PERCENT_RAMP[-1][2]


def compute_position_margin(
    *,
    mode: SizingMode,
    fixed_config: FixedSizingConfig | None = None,
    portfolio_value_usdt: float = 0.0,
    successful_trades: int = 0,
    rng: random.Random | None = None,
) -> float:
    """Top-level dispatch: returns the next trade's margin USDT.

    Used by the bot's signal-emission path: after build_prediction picks
    a direction, this decides "how much".
    """
    if mode == "fixed":
        if fixed_config is None:
            raise ValueError("fixed mode requires fixed_config")
        return fixed_config.resolve(rng=rng)
    if mode == "percent":
        return percent_sizing_amount(
            portfolio_value_usdt=portfolio_value_usdt,
            successful_trades=successful_trades,
        )
    raise ValueError(f"unsupported sizing mode: {mode!r}")


__all__ = [
    "SizingMode",
    "FixedSizingConfig",
    "compute_position_margin",
    "percent_sizing_amount",
]
