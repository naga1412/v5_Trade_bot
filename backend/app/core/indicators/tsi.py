import numpy as np
from numpy.typing import NDArray

from app.core.indicators.ema import ema


def _double_smoothed_ema(
    values: NDArray[np.float64], slow: int, fast: int
) -> NDArray[np.float64]:
    """EMA(EMA(values, slow), fast), preserving NaN-leading positions."""
    n = values.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    first_valid = int(np.argmax(~np.isnan(values))) if not np.all(np.isnan(values)) else n
    if first_valid >= n:
        return out
    e1 = ema(values[first_valid:], slow)
    fv2 = int(np.argmax(~np.isnan(e1))) if not np.all(np.isnan(e1)) else len(e1)
    if fv2 >= len(e1):
        return out
    e2 = ema(e1[fv2:], fast)
    out[first_valid + fv2 :] = e2
    return out


def tsi(
    closes: NDArray[np.float64], slow: int = 25, fast: int = 13
) -> NDArray[np.float64]:
    """True Strength Index = 100 * EMA(EMA(diff, slow), fast) / EMA(EMA(|diff|, slow), fast).

    diff = close - close[-1]. Range roughly [-100, 100]. No look-ahead.
    """
    if min(slow, fast) <= 0:
        raise ValueError("slow and fast must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < 2:
        return out
    diff = np.full(n, np.nan, dtype=np.float64)
    diff[1:] = np.diff(closes)
    abs_diff = np.abs(diff)

    num = _double_smoothed_ema(diff, slow, fast)
    den = _double_smoothed_ema(abs_diff, slow, fast)
    nonzero = den != 0
    out[nonzero] = 100.0 * num[nonzero] / den[nonzero]
    return out
