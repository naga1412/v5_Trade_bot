import numpy as np
from numpy.typing import NDArray


def ema(closes: NDArray[np.float64], period: int) -> NDArray[np.float64]:
    """Exponential moving average.

    Convention: first `period-1` values are NaN; index `period-1` is the SMA
    of the first `period` closes; subsequent values use alpha = 2/(period+1).
    No look-ahead: output[i] depends only on closes[0..i].
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out

    alpha = 2.0 / (period + 1)
    out[period - 1] = closes[:period].mean()
    for i in range(period, n):
        out[i] = alpha * closes[i] + (1 - alpha) * out[i - 1]
    return out
