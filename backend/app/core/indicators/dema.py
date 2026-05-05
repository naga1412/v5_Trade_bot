import numpy as np
from numpy.typing import NDArray

from app.core.indicators.ema import ema


def dema(closes: NDArray[np.float64], period: int = 20) -> NDArray[np.float64]:
    """Double Exponential Moving Average = 2*EMA(closes) - EMA(EMA(closes)).

    Reduces lag of a single EMA. Leading 2*(period-1) values are NaN.
    No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out

    ema1 = ema(closes, period)
    # EMA of EMA needs to skip leading NaN
    first_valid = int(np.argmax(~np.isnan(ema1))) if not np.all(np.isnan(ema1)) else n
    if first_valid >= n:
        return out
    ema2_clean = ema(ema1[first_valid:], period)
    ema2 = np.full(n, np.nan, dtype=np.float64)
    ema2[first_valid:] = ema2_clean
    return 2.0 * ema1 - ema2
