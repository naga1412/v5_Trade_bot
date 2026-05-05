import numpy as np
from numpy.typing import NDArray


def donchian(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    period: int = 20,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Donchian Channels → (upper, middle, lower).

    upper  = max(highs[-period:])
    lower  = min(lows[-period:])
    middle = (upper + lower) / 2

    No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if highs.shape != lows.shape:
        raise ValueError("highs and lows must have identical shape")
    n = highs.shape[0]
    upper = np.full(n, np.nan, dtype=np.float64)
    lower = np.full(n, np.nan, dtype=np.float64)
    middle = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return upper, middle, lower
    for i in range(period - 1, n):
        upper[i] = highs[i - period + 1 : i + 1].max()
        lower[i] = lows[i - period + 1 : i + 1].min()
        middle[i] = (upper[i] + lower[i]) / 2.0
    return upper, middle, lower
