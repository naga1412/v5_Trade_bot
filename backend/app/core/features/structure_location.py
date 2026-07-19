"""W5 structure_location — where within market structure the entry lands.

Two features:

  dist_swing_atr        — min distance to the nearest swing high or low, in ATR14 units.
  retracement_fraction  — how far along the most recent impulse leg the current
                          close sits, clamped [0, 1].

Lookback: 21 bars minimum (swing detection needs warmup). Returns all-None below that.

Reuses `find_swing_highs`, `find_swing_lows`, and `recent_atr` from the
shared chart-pattern helpers — no new swing-detection logic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.chart._helpers import (
    find_swing_highs,
    find_swing_lows,
    recent_atr,
)

_MIN_BARS: int = 21
_SWING_PROMINENCE: float = 0.5
_SWING_DISTANCE: int = 3

_NULL: dict[str, float | None] = {
    "dist_swing_atr": None,
    "retracement_fraction": None,
}


def compute(bars: pd.DataFrame) -> dict[str, float | None]:
    """Return structure-location features for the last bar in *bars*.

    Returns all-None when bars is shorter than _MIN_BARS or when swing
    detection finds no pivots.
    """
    if len(bars) < _MIN_BARS:
        return dict(_NULL)

    idx = len(bars) - 1
    atr = recent_atr(bars, idx)
    if atr <= 0:
        return dict(_NULL)

    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    close = float(bars["close"].iloc[idx])

    sh_indices = find_swing_highs(highs, prominence=_SWING_PROMINENCE, distance=_SWING_DISTANCE)
    sl_indices = find_swing_lows(lows, prominence=_SWING_PROMINENCE, distance=_SWING_DISTANCE)

    result: dict[str, float | None] = dict(_NULL)

    # --- dist_swing_atr ---
    sh_prices = [float(highs[i]) for i in sh_indices]
    sl_prices = [float(lows[i]) for i in sl_indices]
    all_swing_prices = sh_prices + sl_prices
    if all_swing_prices:
        nearest_dist = min(abs(close - p) for p in all_swing_prices)
        result["dist_swing_atr"] = nearest_dist / atr

    # --- retracement_fraction ---
    last_sh_idx = sh_indices[-1] if sh_indices else None
    last_sl_idx = sl_indices[-1] if sl_indices else None

    if last_sh_idx is not None and last_sl_idx is not None:
        if last_sl_idx < last_sh_idx:
            # Most recent swing is HIGH — upward impulse leg: SL → SH
            leg_start = float(lows[last_sl_idx])
            leg_end = float(highs[last_sh_idx])
        else:
            # Most recent swing is LOW — downward impulse leg: SH → SL
            leg_start = float(highs[last_sh_idx])
            leg_end = float(lows[last_sl_idx])

        leg_range = leg_end - leg_start
        if abs(leg_range) > 0:
            fraction = (close - leg_start) / leg_range
            result["retracement_fraction"] = float(np.clip(fraction, 0.0, 1.0))

    return result


__all__ = ["compute"]
