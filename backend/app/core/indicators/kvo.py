import numpy as np
from numpy.typing import NDArray

from app.core.indicators.ema import ema


def kvo(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64],
    fast: int = 34,
    slow: int = 55,
) -> NDArray[np.float64]:
    """Klinger Volume Oscillator = EMA(VolumeForce, fast) - EMA(VolumeForce, slow).

    VolumeForce = volume * sign(trend) * |2 * (dm/cm) - 1| * 100
    where trend flips when typical_price changes direction.

    Identifies long-term volume divergences. No look-ahead.
    """
    if min(fast, slow) <= 0:
        raise ValueError("fast and slow must be positive")
    if not (highs.shape == lows.shape == closes.shape == volumes.shape):
        raise ValueError("highs/lows/closes/volumes must have identical shape")
    n = highs.shape[0]
    if n < 2:
        return np.full(n, np.nan, dtype=np.float64)

    typical = (highs + lows + closes) / 3.0
    trend = np.zeros(n, dtype=np.float64)
    dm = highs - lows  # daily measurement
    cm = np.zeros(n, dtype=np.float64)  # cumulative measurement
    vf = np.zeros(n, dtype=np.float64)

    for i in range(1, n):
        trend[i] = 1.0 if typical[i] > typical[i - 1] else -1.0
        if trend[i] == trend[i - 1]:
            cm[i] = cm[i - 1] + dm[i]
        else:
            cm[i] = dm[i - 1] + dm[i]
        if cm[i] == 0:
            vf[i] = 0.0
        else:
            vf[i] = volumes[i] * trend[i] * abs(2.0 * (dm[i] / cm[i]) - 1.0) * 100.0

    fast_ema = ema(vf, fast)
    slow_ema = ema(vf, slow)
    return fast_ema - slow_ema
