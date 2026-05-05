"""Shared utilities for custom chart-pattern detectors.

These helpers do not maintain state and are safe to call from any pattern's
``detect()``. Most chart patterns share the same swing-detection +
trend-line + ATR machinery, so we centralise it here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.signal import find_peaks


def find_swing_highs(
    arr: NDArray[np.float64],
    *,
    prominence: float = 0.5,
    distance: int = 5,
) -> list[int]:
    """Return indices of local-maximum bars meeting prominence + distance."""
    peaks, _ = find_peaks(arr, prominence=prominence, distance=distance)
    return list(map(int, peaks))


def find_swing_lows(
    arr: NDArray[np.float64],
    *,
    prominence: float = 0.5,
    distance: int = 5,
) -> list[int]:
    """Return indices of local-minimum bars (mirror of ``find_swing_highs``)."""
    troughs, _ = find_peaks(-arr, prominence=prominence, distance=distance)
    return list(map(int, troughs))


def fit_trend_line(
    xs: NDArray[np.float64], ys: NDArray[np.float64]
) -> tuple[float, float]:
    """OLS fit; returns ``(slope, intercept)``.

    For ``len(xs) < 2`` returns ``(0.0, mean(ys))`` (or ``(0.0, 0.0)`` if
    ``ys`` is empty), so detectors can call this on degenerate windows
    without special-casing.
    """
    if xs.shape[0] < 2:
        return 0.0, float(ys.mean()) if ys.size else 0.0
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(slope), float(intercept)


def is_inside_bar(bars: pd.DataFrame, idx: int) -> bool:
    """True iff bar at ``idx`` is fully contained inside bar at ``idx-1``."""
    if idx <= 0 or idx >= len(bars):
        return False
    return bool(
        bars["high"].iloc[idx] <= bars["high"].iloc[idx - 1]
        and bars["low"].iloc[idx] >= bars["low"].iloc[idx - 1]
    )


def bar_body_size(bars: pd.DataFrame, idx: int) -> float:
    """Absolute body size: ``|close - open|`` at ``idx``."""
    return float(abs(bars["close"].iloc[idx] - bars["open"].iloc[idx]))


def bar_total_range(bars: pd.DataFrame, idx: int) -> float:
    """Total bar range: ``high - low`` at ``idx``."""
    return float(bars["high"].iloc[idx] - bars["low"].iloc[idx])


def recent_atr(bars: pd.DataFrame, idx: int, period: int = 14) -> float:
    """Mean true range over the trailing ``period`` bars ending at ``idx``.

    Returns 0.0 if there isn't enough history.
    """
    if idx < period:
        return 0.0
    win = bars.iloc[idx - period + 1 : idx + 1]
    h = win["high"].to_numpy(dtype=float)
    lo = win["low"].to_numpy(dtype=float)
    c = win["close"].to_numpy(dtype=float)
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
    return float(tr.mean())
