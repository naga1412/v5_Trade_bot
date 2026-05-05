import numpy as np
import talib
from numpy.typing import NDArray


def bollinger(
    closes: NDArray[np.float64], period: int = 20, num_std: float = 2.0
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Bollinger Bands → (upper, middle, lower).

    middle = SMA(closes, period)
    upper  = middle + num_std * stdev
    lower  = middle - num_std * stdev
    Uses TA-Lib BBANDS. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if num_std <= 0:
        raise ValueError("num_std must be positive")
    arr = np.ascontiguousarray(closes, dtype=np.float64)
    sma_ma = talib.MA_Type.SMA
    upper, middle, lower = talib.BBANDS(
        arr, timeperiod=period, nbdevup=num_std, nbdevdn=num_std, matype=sma_ma,
    )
    return upper, middle, lower
