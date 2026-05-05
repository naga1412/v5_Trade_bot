import numpy as np
from numpy.typing import NDArray

from app.core.indicators.ema import ema


def force_index(
    closes: NDArray[np.float64], volumes: NDArray[np.float64], period: int = 13
) -> NDArray[np.float64]:
    """Elder's Force Index = EMA( (close - close[-1]) * volume , period ).

    Sign of FI tells buying vs selling pressure. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if closes.shape != volumes.shape:
        raise ValueError("closes and volumes must have identical shape")
    n = closes.shape[0]
    if n < 2:
        return np.full(n, np.nan, dtype=np.float64)
    raw = np.full(n, np.nan, dtype=np.float64)
    raw[1:] = (closes[1:] - closes[:-1]) * volumes[1:]
    # EMA over the part that's defined
    fv = int(np.argmax(~np.isnan(raw))) if not np.all(np.isnan(raw)) else n
    out = np.full(n, np.nan, dtype=np.float64)
    if fv < n:
        smoothed = ema(raw[fv:], period)
        out[fv:] = smoothed
    return out
