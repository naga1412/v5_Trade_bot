import numpy as np
from numpy.typing import NDArray


def aroon(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    period: int = 14,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Aroon Up/Down → (up, down).

    up   = 100 * (period - bars_since_highest_high_in_window) / period
    down = 100 * (period - bars_since_lowest_low_in_window) / period
    Window covers the last `period+1` bars per Aroon convention.
    Range [0, 100]. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if highs.shape != lows.shape:
        raise ValueError("highs and lows must have identical shape")
    n = highs.shape[0]
    up = np.full(n, np.nan, dtype=np.float64)
    down = np.full(n, np.nan, dtype=np.float64)
    if n <= period:
        return up, down
    for i in range(period, n):
        window_high = highs[i - period : i + 1]
        window_low = lows[i - period : i + 1]
        # bars since extreme: 0 means at current bar, period means at oldest bar
        idx_high = int(np.argmax(window_high))  # position in window of latest max
        # use rightmost occurrence to favour most-recent extreme
        idx_high = int(period - np.argmax(window_high[::-1]))
        idx_low = int(period - np.argmin(window_low[::-1]))
        bars_since_high = period - idx_high
        bars_since_low = period - idx_low
        up[i] = 100.0 * (period - bars_since_high) / period
        down[i] = 100.0 * (period - bars_since_low) / period
    return up, down
