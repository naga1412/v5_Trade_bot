import numpy as np
from numpy.typing import NDArray

from app.core.indicators.sma import sma


def awesome_oscillator(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    fast: int = 5,
    slow: int = 34,
) -> NDArray[np.float64]:
    """Awesome Oscillator = SMA(median, fast) - SMA(median, slow).

    median = (high + low) / 2. Bullish when AO > 0 and rising. No look-ahead.
    """
    if min(fast, slow) <= 0:
        raise ValueError("fast and slow must be positive")
    if highs.shape != lows.shape:
        raise ValueError("highs and lows must have identical shape")
    median = (highs + lows) / 2.0
    return sma(median, fast) - sma(median, slow)
