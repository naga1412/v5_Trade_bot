import numpy as np
import talib
from numpy.typing import NDArray


def psar(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    acceleration: float = 0.02,
    maximum: float = 0.2,
) -> NDArray[np.float64]:
    """Parabolic SAR (Stop and Reverse) via TA-Lib.

    Trend-following dot below price (uptrend) or above price (downtrend).
    Uses Wilder's accel-step algorithm. No look-ahead.
    """
    if highs.shape != lows.shape:
        raise ValueError("highs and lows must have identical shape")
    if acceleration <= 0 or maximum <= 0:
        raise ValueError("acceleration and maximum must be positive")
    h = np.ascontiguousarray(highs, dtype=np.float64)
    low = np.ascontiguousarray(lows, dtype=np.float64)
    out: NDArray[np.float64] = talib.SAR(h, low, acceleration=acceleration, maximum=maximum)
    return out
