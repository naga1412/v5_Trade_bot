import numpy as np
from numpy.typing import NDArray


def roc(closes: NDArray[np.float64], period: int = 12) -> NDArray[np.float64]:
    """Rate of Change = 100 * (close - close[-period]) / close[-period].

    Leading `period` values are NaN. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n <= period:
        return out
    prev = closes[:-period]
    curr = closes[period:]
    nonzero = prev != 0
    res = np.full(curr.shape, np.nan, dtype=np.float64)
    res[nonzero] = 100.0 * (curr[nonzero] - prev[nonzero]) / prev[nonzero]
    out[period:] = res
    return out
