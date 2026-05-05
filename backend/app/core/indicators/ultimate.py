import numpy as np
import talib
from numpy.typing import NDArray


def ultimate(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period1: int = 7,
    period2: int = 14,
    period3: int = 28,
) -> NDArray[np.float64]:
    """Ultimate Oscillator (Larry Williams) via TA-Lib.

    Weighted average of three timeframes' (close - true_low) / true_range.
    Range [0, 100]. >70 overbought, <30 oversold. No look-ahead.
    """
    if min(period1, period2, period3) <= 0:
        raise ValueError("all periods must be positive")
    if not (highs.shape == lows.shape == closes.shape):
        raise ValueError("highs/lows/closes must have identical shape")
    h = np.ascontiguousarray(highs, dtype=np.float64)
    low = np.ascontiguousarray(lows, dtype=np.float64)
    c = np.ascontiguousarray(closes, dtype=np.float64)
    out: NDArray[np.float64] = talib.ULTOSC(
        h, low, c, timeperiod1=period1, timeperiod2=period2, timeperiod3=period3
    )
    return out
