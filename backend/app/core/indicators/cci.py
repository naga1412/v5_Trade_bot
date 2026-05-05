import numpy as np
import talib
from numpy.typing import NDArray


def cci(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 20,
) -> NDArray[np.float64]:
    """Commodity Channel Index via TA-Lib.

    CCI = (typical - SMA(typical, n)) / (0.015 * mean_abs_deviation)
    typical = (h + l + c) / 3. Values > 100 = strong upmove. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not (highs.shape == lows.shape == closes.shape):
        raise ValueError("highs/lows/closes must have identical shape")
    h = np.ascontiguousarray(highs, dtype=np.float64)
    low = np.ascontiguousarray(lows, dtype=np.float64)
    c = np.ascontiguousarray(closes, dtype=np.float64)
    out: NDArray[np.float64] = talib.CCI(h, low, c, timeperiod=period)
    return out
