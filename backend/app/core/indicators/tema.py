import numpy as np
from numpy.typing import NDArray

from app.core.indicators.ema import ema


def tema(closes: NDArray[np.float64], period: int = 20) -> NDArray[np.float64]:
    """Triple Exponential Moving Average = 3*EMA1 - 3*EMA2 + EMA3.

    EMA1 = EMA(closes), EMA2 = EMA(EMA1), EMA3 = EMA(EMA2).
    Reduces lag further than DEMA. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out

    ema1 = ema(closes, period)
    fv1 = int(np.argmax(~np.isnan(ema1))) if not np.all(np.isnan(ema1)) else n
    if fv1 >= n:
        return out
    ema2_clean = ema(ema1[fv1:], period)
    ema2 = np.full(n, np.nan, dtype=np.float64)
    ema2[fv1:] = ema2_clean

    fv2 = int(np.argmax(~np.isnan(ema2))) if not np.all(np.isnan(ema2)) else n
    if fv2 >= n:
        return out
    ema3_clean = ema(ema2[fv2:], period)
    ema3 = np.full(n, np.nan, dtype=np.float64)
    ema3[fv2:] = ema3_clean

    return 3.0 * ema1 - 3.0 * ema2 + ema3
