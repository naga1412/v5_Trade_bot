import numpy as np
import talib
from numpy.typing import NDArray


def mfi(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64],
    period: int = 14,
) -> NDArray[np.float64]:
    """Money Flow Index via TA-Lib.

    Volume-weighted RSI. Range [0, 100]. >80 overbought, <20 oversold.
    No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not (highs.shape == lows.shape == closes.shape == volumes.shape):
        raise ValueError("highs/lows/closes/volumes must have identical shape")
    h = np.ascontiguousarray(highs, dtype=np.float64)
    low = np.ascontiguousarray(lows, dtype=np.float64)
    c = np.ascontiguousarray(closes, dtype=np.float64)
    v = np.ascontiguousarray(volumes, dtype=np.float64)
    out: NDArray[np.float64] = talib.MFI(h, low, c, v, timeperiod=period)
    return out
