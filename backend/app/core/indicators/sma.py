import numpy as np
from numpy.typing import NDArray


def sma(closes: NDArray[np.float64], period: int = 20) -> NDArray[np.float64]:
    """Simple Moving Average.

    Convention: first `period-1` values are NaN; index `period-1` is the
    arithmetic mean of the first `period` closes. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out
    # Cumulative-sum trick: O(n) rolling mean
    csum = np.cumsum(closes, dtype=np.float64)
    out[period - 1] = csum[period - 1] / period
    if n > period:
        out[period:] = (csum[period:] - csum[:-period]) / period
    return out
