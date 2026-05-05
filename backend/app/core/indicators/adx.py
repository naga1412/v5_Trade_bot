import numpy as np
import talib
from numpy.typing import NDArray


def adx(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Average Directional Index → (ADX, +DI, -DI) via TA-Lib.

    ADX measures trend strength (0-100). +DI/-DI indicate direction.
    ADX > 25 typically signals a trending market. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not (highs.shape == lows.shape == closes.shape):
        raise ValueError("highs/lows/closes must have identical shape")
    h = np.ascontiguousarray(highs, dtype=np.float64)
    low = np.ascontiguousarray(lows, dtype=np.float64)
    c = np.ascontiguousarray(closes, dtype=np.float64)
    adx_arr: NDArray[np.float64] = talib.ADX(h, low, c, timeperiod=period)
    plus_di: NDArray[np.float64] = talib.PLUS_DI(h, low, c, timeperiod=period)
    minus_di: NDArray[np.float64] = talib.MINUS_DI(h, low, c, timeperiod=period)
    return adx_arr, plus_di, minus_di
