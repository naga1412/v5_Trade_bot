import numpy as np
from numpy.typing import NDArray


def vortex(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    period: int = 14,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Vortex Indicator → (VI+, VI-).

    VM+ = |high[i] - low[i-1]|, VM- = |low[i] - high[i-1]|
    TR  = max(h-l, |h-c[-1]|, |l-c[-1]|)
    VI+ = sum(VM+, n) / sum(TR, n); VI- = sum(VM-, n) / sum(TR, n)

    No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if not (highs.shape == lows.shape == closes.shape):
        raise ValueError("highs/lows/closes must have identical shape")
    n = highs.shape[0]
    vi_plus = np.full(n, np.nan, dtype=np.float64)
    vi_minus = np.full(n, np.nan, dtype=np.float64)
    if n < period + 1:
        return vi_plus, vi_minus

    vm_plus = np.full(n, 0.0, dtype=np.float64)
    vm_minus = np.full(n, 0.0, dtype=np.float64)
    tr = np.full(n, 0.0, dtype=np.float64)
    for i in range(1, n):
        vm_plus[i] = abs(highs[i] - lows[i - 1])
        vm_minus[i] = abs(lows[i] - highs[i - 1])
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    for i in range(period, n):
        sum_tr = tr[i - period + 1 : i + 1].sum()
        if sum_tr == 0:
            continue
        vi_plus[i] = vm_plus[i - period + 1 : i + 1].sum() / sum_tr
        vi_minus[i] = vm_minus[i - period + 1 : i + 1].sum() / sum_tr
    return vi_plus, vi_minus
