import numpy as np
from numpy.typing import NDArray

from app.core.indicators.sma import sma


def ease_of_movement(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    volumes: NDArray[np.float64],
    period: int = 14,
    volume_divisor: float = 10000.0,
) -> NDArray[np.float64]:
    """Ease of Movement (Arms) → SMA-smoothed.

    distance = midpoint[i] - midpoint[i-1]; midpoint = (h+l)/2
    box_ratio = (volume / volume_divisor) / (h - l)
    EMV = distance / box_ratio
    Output = SMA(EMV, period). No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not (highs.shape == lows.shape == volumes.shape):
        raise ValueError("highs/lows/volumes must have identical shape")
    n = highs.shape[0]
    if n < 2:
        return np.full(n, np.nan, dtype=np.float64)
    midpoint = (highs + lows) / 2.0
    rng = highs - lows
    emv_raw = np.full(n, np.nan, dtype=np.float64)
    for i in range(1, n):
        if rng[i] == 0 or volumes[i] == 0:
            emv_raw[i] = 0.0
        else:
            box_ratio = (volumes[i] / volume_divisor) / rng[i]
            emv_raw[i] = (midpoint[i] - midpoint[i - 1]) / box_ratio
    # SMA needs no leading NaN within the window — replace NaN at index 0 with 0 for the rolling pass
    emv_clean = np.where(np.isnan(emv_raw), 0.0, emv_raw)
    sm = sma(emv_clean, period)
    # Mask the first `period - 1` outputs (need at least period valid bars; idx 0 was synthetic 0)
    out = np.full(n, np.nan, dtype=np.float64)
    if n >= period:
        out[period - 1 :] = sm[period - 1 :]
    return out
