import numpy as np
from numpy.typing import NDArray

from app.core.indicators.ema import ema


def macd(
    closes: NDArray[np.float64],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """MACD = EMA(fast) − EMA(slow); signal = EMA(MACD, signal); hist = MACD − signal.

    Returns (macd_line, signal_line, histogram). No look-ahead.
    """
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line = fast_ema - slow_ema
    # Replace NaN with NaN explicitly for clarity (already NaN when subtracted)

    # Signal EMA must skip leading NaN; build a clean view starting at first non-NaN index
    n = closes.shape[0]
    signal_line = np.full(n, np.nan, dtype=np.float64)
    first_valid = int(np.argmax(~np.isnan(macd_line))) if not np.all(np.isnan(macd_line)) else n
    if first_valid < n:
        clean = macd_line[first_valid:]
        sig_clean = ema(clean, signal)
        signal_line[first_valid:] = sig_clean

    hist = macd_line - signal_line
    return macd_line, signal_line, hist
