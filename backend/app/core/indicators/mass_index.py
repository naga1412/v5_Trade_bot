import numpy as np
from numpy.typing import NDArray

from app.core.indicators.ema import ema


def mass_index(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    ema_period: int = 9,
    sum_period: int = 25,
) -> NDArray[np.float64]:
    """Mass Index = sum( EMA9(h-l) / EMA9(EMA9(h-l)) , 25 ).

    Range expansion detector — typically [25, 30]; values > 27 then dropping
    below 26.5 hint at imminent reversal.
    No look-ahead.
    """
    if min(ema_period, sum_period) <= 0:
        raise ValueError("ema_period and sum_period must be positive")
    if highs.shape != lows.shape:
        raise ValueError("highs and lows must have identical shape")
    n = highs.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)

    rng = highs - lows
    e1 = ema(rng, ema_period)
    fv = int(np.argmax(~np.isnan(e1))) if not np.all(np.isnan(e1)) else n
    if fv >= n:
        return out
    e2_clean = ema(e1[fv:], ema_period)
    e2 = np.full(n, np.nan, dtype=np.float64)
    e2[fv:] = e2_clean

    ratio = np.full(n, np.nan, dtype=np.float64)
    nonzero = e2 != 0
    ratio[nonzero] = e1[nonzero] / e2[nonzero]

    # Rolling sum of `ratio` over `sum_period` bars
    for i in range(sum_period - 1, n):
        window = ratio[i - sum_period + 1 : i + 1]
        if not np.isnan(window).any():
            out[i] = float(window.sum())
    return out
