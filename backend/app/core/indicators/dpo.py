import numpy as np
from numpy.typing import NDArray

from app.core.indicators.sma import sma


def dpo(closes: NDArray[np.float64], period: int = 20) -> NDArray[np.float64]:
    """Detrended Price Oscillator: close[i - n/2 + 1] - SMA(close, n)[i].

    Uses backward-shift form (no look-ahead): at bar i, references close at
    i - shift where shift = period//2 + 1. Leading bars are NaN.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out
    sma_vals = sma(closes, period)
    shift = period // 2 + 1
    for i in range(period - 1, n):
        if i - shift >= 0:
            out[i] = closes[i - shift] - sma_vals[i]
    return out
