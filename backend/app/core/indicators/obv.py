import numpy as np
from numpy.typing import NDArray


def obv(closes: NDArray[np.float64], volumes: NDArray[np.float64]) -> NDArray[np.float64]:
    """On-Balance Volume = cumulative sum of volume signed by close direction.

    OBV[0] = 0; for i>=1: OBV[i] = OBV[i-1] + sign(close[i] - close[i-1]) * volume[i].
    No look-ahead.
    """
    if closes.shape != volumes.shape:
        raise ValueError("closes and volumes must have identical shape")
    n = closes.shape[0]
    out = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out
