import numpy as np
import talib
from numpy.typing import NDArray


def stochastic(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    k_period: int = 14,
    d_period: int = 3,
    slowing: int = 3,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Stochastic Oscillator (slow): returns (%K, %D).

    %K = SMA(100 * (close - LL) / (HH - LL), slowing)
    %D = SMA(%K, d_period)
    Uses TA-Lib STOCH (TradingView-default smoothing). No look-ahead.
    """
    if min(k_period, d_period, slowing) <= 0:
        raise ValueError("all periods must be positive")
    if not (highs.shape == lows.shape == closes.shape):
        raise ValueError("highs/lows/closes must have identical shape")
    h = np.ascontiguousarray(highs, dtype=np.float64)
    low = np.ascontiguousarray(lows, dtype=np.float64)
    c = np.ascontiguousarray(closes, dtype=np.float64)
    sma_ma = talib.MA_Type.SMA
    k, d = talib.STOCH(
        h, low, c, k_period, slowing, sma_ma, d_period, sma_ma,
    )
    return k, d
