import numpy as np
import talib
from numpy.typing import NDArray


def trix(closes: NDArray[np.float64], period: int = 15) -> NDArray[np.float64]:
    """TRIX: 1-period rate-of-change of triple-smoothed EMA via TA-Lib.

    Filters out cycles shorter than period; oscillates around zero.
    No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    arr = np.ascontiguousarray(closes, dtype=np.float64)
    out: NDArray[np.float64] = talib.TRIX(arr, timeperiod=period)
    return out
