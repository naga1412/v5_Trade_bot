import numpy as np
import talib
from numpy.typing import NDArray


def kama(closes: NDArray[np.float64], period: int = 30) -> NDArray[np.float64]:
    """Kaufman Adaptive Moving Average via TA-Lib.

    Adapts smoothing to volatility — fast in trending markets, slow in noise.
    Leading bars are NaN per TA-Lib convention. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    arr = np.ascontiguousarray(closes, dtype=np.float64)
    out: NDArray[np.float64] = talib.KAMA(arr, timeperiod=period)
    return out
