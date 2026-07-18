"""W2 volatility-state feature computer.

Pure function — no async, no DB, no side effects. Returns a dict with
float values (or None when bar history is too short). Called at two sites:
  - predictor.py: stashes result in prediction_extras["features"]
  - shadow/observation.py: stores result in obs_components["features"]

Spec: docs/superpowers/specs/2026-07-18-brain-supervisor-expansion.md §3.3
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_MIN_BARS_REALIZED = 25   # 24 log-returns requires 25 closes
_MIN_BARS_ATR_RATIO = 35  # 15-bar ATR window + 20-bar offset back in time
_MIN_BARS_PERCENTILE = 744  # 720 rolling 24-window samples need 720+24 closes


def compute(bars: pd.DataFrame) -> dict[str, float | None]:
    """Return W2 volatility-state features for the latest bar.

    Keys:
        realized_vol_24bar: annualized σ of last-24 1h log-returns (√8760 scale).
        vol_percentile_30d: fraction of rolling 720-window history with vol ≤ current.
                            None when fewer than 744 bars are available.
        atr_expansion_ratio: ATR14_now / ATR14_20bars_ago. >1 = expanding.
                             None when fewer than 35 bars are available.
    """
    NULL: dict[str, float | None] = {
        "realized_vol_24bar": None,
        "vol_percentile_30d": None,
        "atr_expansion_ratio": None,
    }
    n = len(bars)
    if n < _MIN_BARS_REALIZED:
        return NULL

    closes = bars["close"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)

    log_returns = np.diff(np.log(np.maximum(closes, 1e-12)))  # guard log(0)

    rv = float(np.std(log_returns[-24:], ddof=1)) * np.sqrt(8760.0)
    realized_vol_24bar: float | None = rv if np.isfinite(rv) else None

    vol_percentile: float | None = None
    if n >= _MIN_BARS_PERCENTILE and realized_vol_24bar is not None:
        rolling_vols = (
            pd.Series(log_returns).rolling(24).std(ddof=1).dropna().values
            * np.sqrt(8760.0)
        )
        if len(rolling_vols) >= 720:
            window_720 = rolling_vols[-720:]
            vol_percentile = float(np.mean(window_720 <= realized_vol_24bar))

    atr_expansion: float | None = None
    if n >= _MIN_BARS_ATR_RATIO:
        atr_now = _atr14(closes[-15:], highs[-15:], lows[-15:])
        atr_ago = _atr14(closes[-35:-20], highs[-35:-20], lows[-35:-20])
        if atr_ago > 0:
            atr_expansion = float(atr_now / atr_ago)

    return {
        "realized_vol_24bar": realized_vol_24bar,
        "vol_percentile_30d": vol_percentile,
        "atr_expansion_ratio": atr_expansion,
    }


def _atr14(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    prev_c = np.concatenate([[closes[0]], closes[:-1]])
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)))
    return float(np.mean(tr[-period:]))
