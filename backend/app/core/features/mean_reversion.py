"""W1 mean-reversion feature computer.

Pure function — no async, no DB, no side effects. Returns a dict with
float values (or None when the bar history is too short). Called at two
sites with the same ``bars`` DataFrame to guarantee train/serve alignment:
  - predictor.py: stashes result in prediction_extras["features"]["W1"]
  - shadow/observation.py: stores result in obs_components["features"]

Spec: docs/superpowers/specs/2026-07-18-brain-supervisor-expansion.md §3.2
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_MIN_BARS = 21


def compute(bars: pd.DataFrame) -> dict[str, float | None]:
    """Return W1 mean-reversion features for the latest bar.

    Keys:
        z_ext: (close − EMA20) / ATR14, clamped [-5, 5].
        bollinger_pct_b: %B = (close − lower) / (upper − lower), clamped [-0.5, 1.5].
        dist_7d_high_pct: (high_7d − close) / close. 0 = at the 7-day high.

    Returns dict with all-None values when len(bars) < 21.
    """
    NULL: dict[str, float | None] = {
        "z_ext": None, "bollinger_pct_b": None, "dist_7d_high_pct": None,
    }
    if len(bars) < _MIN_BARS:
        return NULL

    closes = bars["close"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)

    ema20 = _ema(closes, 20)
    atr14 = _atr14(closes, bars["high"].to_numpy(dtype=float), bars["low"].to_numpy(dtype=float))
    if atr14 <= 0:
        return NULL

    last = closes[-1]

    z_ext = float(np.clip((last - ema20) / atr14, -5.0, 5.0))

    last_20 = closes[-20:]
    std20 = float(np.std(last_20, ddof=0))
    upper = ema20 + 2.0 * std20
    lower = ema20 - 2.0 * std20
    band_width = upper - lower
    pct_b = float(np.clip((last - lower) / band_width, -0.5, 1.5)) if band_width > 0 else 0.5

    lookback = min(168, len(highs))
    high_7d = float(np.max(highs[-lookback:]))
    dist_7d = float((high_7d - last) / last) if last > 0 else 0.0

    return {"z_ext": z_ext, "bollinger_pct_b": pct_b, "dist_7d_high_pct": dist_7d}


def _ema(data: np.ndarray, period: int) -> float:
    """Single-pass EMA returning only the final value."""
    alpha = 2.0 / (period + 1)
    val = data[0]
    for x in data[1:]:
        val = alpha * x + (1.0 - alpha) * val
    return val


def _atr14(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    prev_c = np.concatenate([[closes[0]], closes[:-1]])
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)))
    return float(np.mean(tr[-period:]))
