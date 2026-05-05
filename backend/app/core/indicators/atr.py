import numpy as np
import talib
from numpy.typing import NDArray


def atr(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14,
) -> NDArray[np.float64]:
    """Average True Range with Wilder smoothing via TA-Lib.

    TR = max(h-l, |h-prev_close|, |l-prev_close|).
    ATR = Wilder-smoothed TR over `period` bars. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not (highs.shape == lows.shape == closes.shape):
        raise ValueError("highs/lows/closes must have identical shape")
    h = np.ascontiguousarray(highs, dtype=np.float64)
    low = np.ascontiguousarray(lows, dtype=np.float64)
    c = np.ascontiguousarray(closes, dtype=np.float64)
    out: NDArray[np.float64] = talib.ATR(h, low, c, timeperiod=period)
    return out
