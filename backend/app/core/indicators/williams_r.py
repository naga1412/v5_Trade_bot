import numpy as np
from numpy.typing import NDArray


def williams_r(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14,
) -> NDArray[np.float64]:
    """Williams %R = -100 * (HH_n - close) / (HH_n - LL_n).

    Range: [-100, 0]. -20 = overbought, -80 = oversold. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not (highs.shape == lows.shape == closes.shape):
        raise ValueError("highs/lows/closes must have identical shape")
    n = highs.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out
    for i in range(period - 1, n):
        hh = highs[i - period + 1 : i + 1].max()
        ll = lows[i - period + 1 : i + 1].min()
        rng = hh - ll
        out[i] = 0.0 if rng == 0 else -100.0 * (hh - closes[i]) / rng
    return out
