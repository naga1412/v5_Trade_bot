import numpy as np
from numpy.typing import NDArray


def chaikin_money_flow(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64],
    period: int = 20,
) -> NDArray[np.float64]:
    """Chaikin Money Flow = sum(MFV, n) / sum(volume, n).

    money_flow_multiplier = ((c-l) - (h-c)) / (h-l)
    money_flow_volume     = mfm * volume
    Range [-1, 1]. >0 buying pressure dominant. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not (highs.shape == lows.shape == closes.shape == volumes.shape):
        raise ValueError("highs/lows/closes/volumes must have identical shape")
    n = highs.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out

    rng = highs - lows
    mfm = np.full(n, 0.0, dtype=np.float64)
    nonzero = rng != 0
    mfm[nonzero] = ((closes[nonzero] - lows[nonzero]) - (highs[nonzero] - closes[nonzero])) / rng[nonzero]
    mfv = mfm * volumes
    for i in range(period - 1, n):
        sum_v = volumes[i - period + 1 : i + 1].sum()
        if sum_v == 0:
            continue
        out[i] = mfv[i - period + 1 : i + 1].sum() / sum_v
    return out
