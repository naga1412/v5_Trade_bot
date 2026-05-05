import math

import numpy as np
from numpy.typing import NDArray


def _wma(closes: NDArray[np.float64], period: int) -> NDArray[np.float64]:
    """Weighted Moving Average with linearly increasing weights 1..period."""
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period or period <= 0:
        return out
    weights = np.arange(1, period + 1, dtype=np.float64)
    denom = weights.sum()
    for i in range(period - 1, n):
        out[i] = float(np.dot(closes[i - period + 1 : i + 1], weights) / denom)
    return out


def hull_ma(closes: NDArray[np.float64], period: int = 20) -> NDArray[np.float64]:
    """Hull Moving Average = WMA(2*WMA(closes, n/2) - WMA(closes, n), sqrt(n)).

    Reduces lag aggressively while smoothing. No look-ahead.
    """
    if period <= 1:
        raise ValueError("period must be > 1")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out

    half = max(1, period // 2)
    sqrt_p = max(1, int(math.sqrt(period)))

    wma_half = _wma(closes, half)
    wma_full = _wma(closes, period)
    raw = 2.0 * wma_half - wma_full

    # WMA over the raw series — but raw has leading NaN; only feed valid tail
    first_valid = int(np.argmax(~np.isnan(raw))) if not np.all(np.isnan(raw)) else n
    if first_valid >= n:
        return out
    smoothed = _wma(raw[first_valid:], sqrt_p)
    out[first_valid:] = smoothed
    return out
