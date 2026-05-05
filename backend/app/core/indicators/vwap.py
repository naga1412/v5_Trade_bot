import numpy as np
from numpy.typing import NDArray


def vwap(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Volume-Weighted Average Price (cumulative).

    typical_price = (high + low + close) / 3
    vwap[i] = sum(typical * volume)[..i] / sum(volume)[..i]

    Cumulative-from-start variant (intraday session VWAP). No look-ahead.
    """
    if not (highs.shape == lows.shape == closes.shape == volumes.shape):
        raise ValueError("highs/lows/closes/volumes must have identical shape")
    typical = (highs + lows + closes) / 3.0
    pv = typical * volumes
    cum_pv = np.cumsum(pv, dtype=np.float64)
    cum_v = np.cumsum(volumes, dtype=np.float64)
    out = np.full(highs.shape[0], np.nan, dtype=np.float64)
    nonzero = cum_v != 0
    out[nonzero] = cum_pv[nonzero] / cum_v[nonzero]
    return out
