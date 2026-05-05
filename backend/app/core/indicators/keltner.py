import numpy as np
from numpy.typing import NDArray

from app.core.indicators.atr import atr
from app.core.indicators.ema import ema


def keltner(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Keltner Channels → (upper, middle, lower).

    middle = EMA(close, ema_period)
    upper  = middle + multiplier * ATR(atr_period)
    lower  = middle - multiplier * ATR(atr_period)

    No look-ahead.
    """
    if min(ema_period, atr_period) <= 0:
        raise ValueError("ema_period and atr_period must be positive")
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")
    if not (highs.shape == lows.shape == closes.shape):
        raise ValueError("highs/lows/closes must have identical shape")
    middle = ema(closes, ema_period)
    a = atr(highs, lows, closes, atr_period)
    upper = middle + multiplier * a
    lower = middle - multiplier * a
    return upper, middle, lower
